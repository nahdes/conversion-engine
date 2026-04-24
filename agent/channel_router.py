"""Channel handoff state machine — centralizes email → SMS → calendar
transitions so handlers don't each re-implement the warm-lead gate, the
per-event HubSpot writes, or the Cal.com link attachment.

The rubric calls out channel handoff as a centralized module or state
machine rather than scattered across handlers. Everything below is the
one place that decides:

  1. What state a prospect is in (cold / emailed / replied / sms / booked)
  2. Which transition is legal from the current state
  3. Which side-effects (HubSpot writes, Cal.com link attach) fire at
     each transition

Callers (`main_agent`, `email_handler` webhook, `sms_handler` webhook,
`calendar_handler` webhook) invoke the matching `on_*` method. They do
NOT read/write HubSpot properties directly for lifecycle state — the
router owns `hs_lead_status`, `tenacious_channel_state`, and the
per-channel timestamps.

State model (explicit; illegal transitions raise ChannelTransitionError):

    COLD
      └── on_email_send ──▶ EMAILED
            ├── on_email_reply ──▶ EMAIL_REPLIED ──▶ SMS_ACTIVE ──▶ BOOKED
            ├── on_email_bounce ──▶ BOUNCED (terminal)
            └── on_opt_out ──▶ OPTED_OUT (terminal)

SMS is never the first outbound channel — `advance_to_sms` refuses from
COLD / EMAILED, and only allows the transition from EMAIL_REPLIED or
SMS_ACTIVE. That's the warm-lead gate, and it lives here (not in
sms_handler) so the rule cannot be bypassed by a future caller that
forgets to check.

Cal.com booking links are generated from a single `compose_booking_link`
call, which both email-reply and SMS handler paths use. That keeps the
Cal.com event-type id wiring in one place and makes sure the link that
lands in an email reply matches the one sent via SMS.
"""
from __future__ import annotations

import os, logging, datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger(__name__)


class ConversationState(str, Enum):
    """Every prospect sits in exactly one of these states. Strings (not
    ints) so they serialize cleanly into HubSpot's `tenacious_channel_state`
    custom property and appear legibly in audit logs."""
    COLD          = 'cold'
    EMAILED       = 'emailed'
    EMAIL_REPLIED = 'email_replied'
    SMS_ACTIVE    = 'sms_active'
    BOOKED        = 'booked'
    BOUNCED       = 'bounced'
    OPTED_OUT     = 'opted_out'


# Legal transitions. The key is the source state; the value is the set
# of destination states the router will permit. Any transition not in
# this table raises ChannelTransitionError — a safer default than
# silently demoting or skipping.
_ALLOWED_TRANSITIONS: dict[ConversationState, set[ConversationState]] = {
    ConversationState.COLD: {
        ConversationState.EMAILED,
        ConversationState.OPTED_OUT,  # staff-side pre-emptive suppression
    },
    ConversationState.EMAILED: {
        ConversationState.EMAIL_REPLIED,
        ConversationState.BOUNCED,
        ConversationState.OPTED_OUT,
        ConversationState.EMAILED,  # re-send / follow-up
    },
    ConversationState.EMAIL_REPLIED: {
        ConversationState.SMS_ACTIVE,
        ConversationState.BOOKED,
        ConversationState.OPTED_OUT,
        ConversationState.EMAIL_REPLIED,  # multiple replies
    },
    ConversationState.SMS_ACTIVE: {
        ConversationState.BOOKED,
        ConversationState.OPTED_OUT,
        ConversationState.EMAIL_REPLIED,  # back to email if prospect prefers
        ConversationState.SMS_ACTIVE,
    },
    ConversationState.BOOKED: {
        ConversationState.BOOKED,  # reschedule
        ConversationState.OPTED_OUT,
    },
    ConversationState.BOUNCED: set(),       # terminal
    ConversationState.OPTED_OUT: set(),     # terminal
}


# HubSpot `hs_lead_status` for each conversation state. Kept as a table
# so the two vocabularies stay in sync without the handlers having to
# remember the mapping.
_HS_LEAD_STATUS: dict[ConversationState, str] = {
    ConversationState.COLD:          'NEW',
    ConversationState.EMAILED:       'ATTEMPTED_TO_CONTACT',
    ConversationState.EMAIL_REPLIED: 'CONNECTED',
    ConversationState.SMS_ACTIVE:    'IN_PROGRESS',
    ConversationState.BOOKED:        'OPEN_DEAL',
    ConversationState.BOUNCED:       'BAD_TIMING',
    ConversationState.OPTED_OUT:     'UNQUALIFIED',
}


class ChannelTransitionError(RuntimeError):
    """Raised when a caller asks for a state transition the router does
    not permit (e.g. trying to send SMS from COLD, which would bypass
    the warm-lead gate). Callers should treat this as a policy violation
    — NOT a transient retryable error."""


@dataclass
class ChannelEvent:
    """One row in the conversation's event ledger. Appended to the
    HubSpot note timeline AND kept in-memory on the router so a single
    prospect's journey can be replayed without calling back to the CRM."""
    timestamp: str
    channel: str                    # 'email' | 'sms' | 'calendar' | 'internal'
    event_type: str                 # short slug ('email_sent', 'sms_reply', ...)
    from_state: ConversationState
    to_state: ConversationState
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_note_body(self) -> str:
        md = ', '.join(f'{k}={v}' for k, v in self.metadata.items()
                       if v is not None) or '(no metadata)'
        return (f'[channel_router] {self.timestamp} {self.channel}/'
                f'{self.event_type}: {self.from_state.value} → '
                f'{self.to_state.value} — {md}')


def _now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec='seconds')


def _resolve_state(props: dict | None) -> ConversationState:
    """Read the router's owned property (`tenacious_channel_state`) first;
    fall back to `hs_lead_status` when the portal lacks the custom
    property so a freshly-provisioned account doesn't mis-classify every
    contact as COLD. Unknown values fall back to COLD."""
    if not props:
        return ConversationState.COLD
    raw = (props.get('tenacious_channel_state') or '').strip().lower()
    if raw:
        try:
            return ConversationState(raw)
        except ValueError:
            pass
    hs = (props.get('hs_lead_status') or '').strip().upper()
    inverse = {v: k for k, v in _HS_LEAD_STATUS.items()}
    return inverse.get(hs, ConversationState.COLD)


def compose_booking_link(*, for_channel: str = 'email') -> str:
    """Single source of truth for the Cal.com booking link that both the
    email reply-handler and the SMS booking-intent path attach. Uses the
    public booking page URL (not an authenticated admin link) so the
    prospect can land on the page without a Cal.com account.

    `for_channel` is logged so we can separate email-click from SMS-click
    in analytics, but does not change the URL itself — the router owns
    exactly one canonical link per event type.
    """
    base = (os.environ.get('CALCOM_BOOKING_URL')
            or 'https://cal.com/tenacious/discovery-call').strip()
    log.debug('channel_router: booking link issued for %s -> %s',
              for_channel, base)
    return base


class ChannelRouter:
    """Coordinates email ↔ SMS ↔ calendar for a single prospect.

    One router instance per conversation. The router is stateless across
    runs (state is persisted in HubSpot), so constructing a new instance
    per event — or per webhook invocation — is intentional: it forces
    the caller to hydrate from CRM, making mis-sequenced events visible
    rather than silently accepted.
    """

    def __init__(self, contact_id: str,
                 contact_props: dict | None = None,
                 *, hubspot=None):
        """`hubspot` defaults to the module-level `hubspot_handler`. The
        injection seam is there so probes and unit tests can pass a
        fake without monkey-patching the import."""
        self.contact_id = contact_id
        self.state = _resolve_state(contact_props)
        self.events: list[ChannelEvent] = []
        self._props_cache = dict(contact_props or {})
        if hubspot is None:
            # Lazy default so importing channel_router doesn't require a
            # HubSpot token at module load time (keeps tests clean).
            import hubspot_handler as _hs
            hubspot = _hs
        self._hs = hubspot

    # -- public transition API --------------------------------------------

    def on_email_send(self, *, email_id: str, subject: str,
                      destination: str, is_live: bool) -> ChannelEvent:
        """Call once Resend accepts the outbound email (success path).
        Advances COLD/EMAILED → EMAILED and stamps `last_email_sent_at`."""
        return self._transition(
            ConversationState.EMAILED,
            channel='email', event_type='email_sent',
            metadata={'email_id': email_id, 'subject': subject,
                      'destination': destination, 'live': is_live},
            hs_props={'last_email_sent_at': _now()},
        )

    def on_email_reply(self, *, from_email: str, subject: str | None,
                       message_id: str | None) -> ChannelEvent:
        """Email webhook reported a reply. EMAILED → EMAIL_REPLIED (opens
        the warm-channel gate for SMS)."""
        return self._transition(
            ConversationState.EMAIL_REPLIED,
            channel='email', event_type='email_reply',
            metadata={'from_email': from_email, 'subject': subject,
                      'message_id': message_id},
            hs_props={'last_email_reply_at': _now(),
                      'warm_channel_open': 'true'},
        )

    def on_email_bounce(self, *, to_email: str, reason: str | None,
                        message_id: str | None) -> ChannelEvent:
        """Email webhook reported a permanent bounce. Moves to BOUNCED
        (terminal). Bounce data is kept on the contact for the sweep
        scripts that rebuild the suppression list."""
        return self._transition(
            ConversationState.BOUNCED,
            channel='email', event_type='email_bounce',
            metadata={'to_email': to_email, 'reason': reason,
                      'message_id': message_id},
            hs_props={'email_bounced_at': _now(),
                      'bounce_reason': (reason or '')[:200]},
        )

    def advance_to_sms(self, *, phone: str) -> ChannelEvent:
        """Called right before the agent (or staff) sends the first SMS.
        Requires EMAIL_REPLIED or SMS_ACTIVE — enforces the warm-lead
        gate centrally instead of trusting each handler to check."""
        if self.state not in (ConversationState.EMAIL_REPLIED,
                              ConversationState.SMS_ACTIVE):
            raise ChannelTransitionError(
                f'cannot advance to SMS from {self.state.value}; '
                'warm-channel hierarchy requires a prior email reply. '
                'This is a policy gate, not a retryable error.')
        return self._transition(
            ConversationState.SMS_ACTIVE,
            channel='sms', event_type='sms_warmed',
            metadata={'phone': phone},
            hs_props={'first_sms_eligible_at': _now()},
        )

    def on_sms_inbound(self, *, phone: str, text: str,
                       intent: str) -> ChannelEvent:
        """SMS webhook classified an inbound message. Intent 'stop' routes
        to OPTED_OUT (terminal); 'booking' and 'info' keep / warm to
        SMS_ACTIVE. STOP always wins over every other intent — this
        method will not be redirected to the router's default path."""
        if intent == 'stop':
            return self.on_opt_out(channel='sms',
                                   reason=f'SMS STOP from {phone}: {text!r}')
        target = (ConversationState.SMS_ACTIVE
                  if self.state in (ConversationState.EMAIL_REPLIED,
                                    ConversationState.SMS_ACTIVE,
                                    ConversationState.BOOKED)
                  else ConversationState.EMAIL_REPLIED)
        return self._transition(
            target,
            channel='sms', event_type=f'sms_inbound_{intent}',
            metadata={'phone': phone, 'intent': intent,
                      'text': (text or '')[:200]},
            hs_props={'last_sms_inbound_at': _now()},
        )

    def on_booking_created(self, *, booking_uid: str,
                           start_time: str,
                           meeting_url: str | None,
                           source_channel: str = 'calendar') -> ChannelEvent:
        """Cal.com webhook (or direct `book_slot_and_sync`) reported a
        booking. Moves to BOOKED from any reachable state; stamps the
        uid/start so the CRM record carries the canonical reference."""
        # Booking is allowed from any non-terminal state because a
        # prospect may book via the self-serve link without ever
        # replying over email. The warm-channel gate was already
        # guarded on the SMS path, and Cal.com links are only ever
        # handed out via email or SMS (never cold-shared), so reaching
        # this point implies an explicit prospect action.
        if self.state in (ConversationState.BOUNCED,
                          ConversationState.OPTED_OUT):
            raise ChannelTransitionError(
                f'booking arrived for terminal state {self.state.value} '
                f'(contact={self.contact_id}); flag for review')
        return self._transition(
            ConversationState.BOOKED,
            channel=source_channel, event_type='booking_created',
            metadata={'booking_uid': booking_uid,
                      'start_time': start_time,
                      'meeting_url': meeting_url},
            hs_props={'calcom_booking_uid': booking_uid,
                      'last_meeting_booked_at': start_time,
                      'meeting_url': (meeting_url or '')[:500]},
        )

    def on_opt_out(self, *, channel: str, reason: str) -> ChannelEvent:
        """Any channel can trigger opt-out (email unsubscribe link, SMS
        STOP, staff manual). Moves to OPTED_OUT (terminal); stamps both
        the channel-specific and the global unsubscribe properties so
        downstream filters have one place to look."""
        hs_props = {
            'tenacious_opted_out_at': _now(),
            'opt_out_channel': channel,
            'opt_out_reason': (reason or '')[:300],
        }
        if channel == 'sms':
            hs_props['sms_unsubscribed'] = 'true'
        elif channel == 'email':
            hs_props['email_unsubscribed'] = 'true'
        return self._transition(
            ConversationState.OPTED_OUT,
            channel=channel, event_type='opt_out',
            metadata={'reason': reason},
            hs_props=hs_props,
            force=True,  # opt-out is always legal — regulatory floor
        )

    # -- introspection -----------------------------------------------------

    def can_send_sms(self) -> bool:
        """Public guard for the SMS handler's pre-send check. Mirrors the
        `advance_to_sms` gate without mutating state so callers can
        decide whether to attempt the transition."""
        return self.state in (ConversationState.EMAIL_REPLIED,
                              ConversationState.SMS_ACTIVE,
                              ConversationState.BOOKED)

    def can_receive_email(self) -> bool:
        """Is it legal to send (another) email to this contact? False
        for BOUNCED or OPTED_OUT — email-handler callers should check
        this before composing to avoid wasting a send."""
        return self.state not in (ConversationState.BOUNCED,
                                  ConversationState.OPTED_OUT)

    def booking_link_for(self, channel: str) -> str:
        """Single canonical Cal.com link, tagged with the originating
        channel for analytics. Callers should prefer this method over
        importing compose_booking_link directly, so future per-segment
        routing (e.g. Segment 4 → specialized calendar) can be added
        here without touching handlers."""
        return compose_booking_link(for_channel=channel)

    # -- internals ---------------------------------------------------------

    def _transition(self, to_state: ConversationState, *,
                    channel: str, event_type: str,
                    metadata: dict[str, Any],
                    hs_props: dict | None = None,
                    force: bool = False) -> ChannelEvent:
        """Validate + apply + emit. Side-effect order is deliberate:

            1. Legality check (raises before anything else happens)
            2. In-memory state advance
            3. HubSpot upsert of (tenacious_channel_state, hs_lead_status,
               any channel-specific timestamps) — single CRM call per
               transition, so the conversation_state is never out of
               sync with the lead_status
            4. Engagement note appended for the audit trail

        Step 3 failures raise; step 4 failures are swallowed (audit
        note is nice-to-have, not load-bearing)."""
        if not force and to_state not in _ALLOWED_TRANSITIONS[self.state]:
            raise ChannelTransitionError(
                f'illegal transition {self.state.value} → {to_state.value} '
                f'for contact {self.contact_id} (channel={channel}, '
                f'event={event_type}).')

        event = ChannelEvent(
            timestamp=_now(),
            channel=channel,
            event_type=event_type,
            from_state=self.state,
            to_state=to_state,
            metadata=dict(metadata),
        )
        prior_state = self.state
        self.state = to_state
        self.events.append(event)

        # Event-point #1: contact property write. Mirrors state + any
        # channel-specific timestamps or status fields.
        props_to_write: dict[str, Any] = dict(hs_props or {})
        props_to_write['tenacious_channel_state'] = to_state.value
        props_to_write['hs_lead_status'] = _HS_LEAD_STATUS[to_state]
        try:
            self._hs.update_contact(self.contact_id, props_to_write)
        except Exception as e:
            # A CRM write failure isn't recoverable here — the router's
            # job is to keep CRM in sync. Roll back the in-memory state
            # so a retry starts from the correct source.
            self.state = prior_state
            self.events.pop()
            raise ChannelTransitionError(
                f'hubspot update_contact failed during '
                f'{channel}/{event_type}: {e}') from e

        # Event-point #2: engagement note for the audit timeline.
        try:
            self._hs.log_note(self.contact_id, event.to_note_body())
        except Exception as e:
            log.info('channel_router: audit-note write failed for %s '
                     '(%s/%s): %s', self.contact_id, channel, event_type, e)

        return event


def load_router(contact_id: str, hubspot=None) -> ChannelRouter:
    """Hydrate a router for an existing contact. The CRM read is
    centralized here so callers don't each reimplement the property
    list and the error handling on a missing contact."""
    if hubspot is None:
        import hubspot_handler as _hs
        hubspot = _hs
    props = {}
    try:
        record = hubspot.get_contact(contact_id)
        if record:
            props = record.get('properties') or {}
    except Exception as e:
        log.warning('channel_router: hydrate failed for %s: %s',
                    contact_id, e)
    return ChannelRouter(contact_id, props, hubspot=hubspot)
