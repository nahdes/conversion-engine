"""SMS handler — Africa's Talking send + inbound routing.

SMS is the warm-channel leg of the stack. Two load-bearing behaviors
beyond "post to AT" and "parse the payload":

1. Warm-channel hierarchy (outbound): SMS is refused for cold contacts.
   Cold outreach opens on email; SMS only fires once the prospect has
   reached a warm state in HubSpot (CONNECTED / OPEN_DEAL / IN_PROGRESS)
   or has explicitly set `sms_opt_in=true`. Honors the same
   `TENACIOUS_OUTBOUND_ENABLED` kill-switch as email and routes to
   `STAFF_SINK_SMS` when it is not "1".

2. Inbound routing: every incoming SMS is parsed, intent-classified
   (STOP / BOOKING / INFO / OTHER), and mirrored to HubSpot — the
   matching contact (by phone) is warmed, the message is logged as a
   NOTE, and unsubscribe intent is recorded on the contact itself. The
   handler returns a structured action so the FastAPI webhook can
   decide whether to auto-reply, forward a booking link, or stay quiet.
"""
from __future__ import annotations

import os, re, logging
from typing import Any
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

# Intent lexicons. Matched as whole uppercased tokens against the
# normalized SMS body, so "stop." and "Stop please" both resolve cleanly.
STOP_WORDS    = {'STOP', 'UNSUBSCRIBE', 'UNSUB', 'CANCEL', 'END', 'QUIT',
                 'OPTOUT', 'OPT-OUT'}
BOOKING_WORDS = {'BOOK', 'SCHEDULE', 'CALL', 'MEET', 'MEETING', 'YES',
                 'INTERESTED', 'DEMO'}
INFO_WORDS    = {'INFO', 'WHO', 'WHAT', 'WHY', 'HELP', 'MORE', 'DETAILS'}

# HubSpot lead-status values that qualify as "warm" for the SMS
# hierarchy. Cold contacts (NEW / empty) are email-only.
WARM_STAGES = {'CONNECTED', 'OPEN_DEAL', 'IN_PROGRESS', 'OPEN'}

# Normalized tokens for phone numbers. AT returns E.164 most of the time
# but we don't want a leading-zero / space difference to miss a contact.
_NON_DIGIT_RE = re.compile(r'[^\d+]')


class SmsError(RuntimeError):
    """An SMS send failed — bad config, cold-channel refusal, unsubscribed
    contact, or AT rejection. Carries a `reason` code so callers can
    branch without string-matching the message."""

    def __init__(self, message: str, *, reason: str = 'error'):
        super().__init__(message)
        self.reason = reason


def _get_sms_client():
    """Lazy AT SDK init. Keeps the SDK import off the module load path so
    tests can import this module without AT creds configured."""
    import africastalking  # noqa: WPS433 — intentional lazy import
    user = os.environ.get('AT_USERNAME')
    key  = os.environ.get('AT_API_KEY')
    if not user or not key:
        raise SmsError('AT_USERNAME / AT_API_KEY not configured',
                       reason='missing_config')
    africastalking.initialize(user, key)
    return africastalking.SMS


def _normalize_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    cleaned = _NON_DIGIT_RE.sub('', phone.strip())
    return cleaned or None


def _classify_intent(text: str) -> str:
    """Return 'stop' | 'booking' | 'info' | 'other'. STOP wins over every
    other intent so an ambiguous 'STOP calling me' is treated as opt-out."""
    if not text:
        return 'other'
    tokens = set(re.findall(r"[A-Z][A-Z\-]+", text.upper()))
    if tokens & STOP_WORDS:
        return 'stop'
    if tokens & BOOKING_WORDS:
        return 'booking'
    if tokens & INFO_WORDS:
        return 'info'
    return 'other'


def _resolve_sms_destination(phone: str) -> tuple[str, bool]:
    """Kill-switch gate for SMS. Mirrors `main_agent.resolve_destination`
    but keyed to the SMS sink. When `TENACIOUS_OUTBOUND_ENABLED != '1'`,
    every outbound SMS is routed to `STAFF_SINK_SMS`."""
    live = os.environ.get('TENACIOUS_OUTBOUND_ENABLED', '').strip() == '1'
    if live:
        return phone, True
    return os.environ.get('STAFF_SINK_SMS', phone), False


def _is_warm(contact: dict | None) -> bool:
    """A contact qualifies for outbound SMS when any of:
      - `sms_opt_in` is explicitly truthy (highest precedence)
      - `hs_lead_status` is in the warm set
    `sms_unsubscribed=true` always wins over both and disqualifies."""
    if not contact:
        return False
    props = contact.get('properties') or {}
    if str(props.get('sms_unsubscribed', '')).lower() in ('true', '1', 'yes'):
        return False
    if str(props.get('sms_opt_in', '')).lower() in ('true', '1', 'yes'):
        return True
    return (props.get('hs_lead_status') or '').upper() in WARM_STAGES


def send_sms(phone: str, message: str,
             *, contact_id: str | None = None,
             allow_cold: bool = False) -> dict:
    """Send an SMS, honoring the warm-channel hierarchy + kill-switch.

    Routing precedence:
      1. Validate inputs and AT config.
      2. Unless `allow_cold=True`, look up the contact (by id if given,
         else by phone) and require warm status. A cold contact raises
         SmsError(reason='cold_contact_refused'); an unsubscribed
         contact raises SmsError(reason='unsubscribed').
      3. Pass the destination through `_resolve_sms_destination`. If the
         kill-switch is off, the real number is replaced with
         `STAFF_SINK_SMS` — the send still happens so the staff sink
         receives a copy for review.

    `allow_cold=True` is for genuine one-off administrative sends
    (integration probe, staff notifications); production prospect
    outreach must leave it at the default.
    """
    phone = _normalize_phone(phone) or ''
    if not phone:
        raise SmsError('phone is required', reason='bad_input')
    if not message or not message.strip():
        raise SmsError('message is required', reason='bad_input')
    shortcode = os.environ.get('AT_SHORTCODE')
    if not shortcode:
        raise SmsError('AT_SHORTCODE not configured', reason='missing_config')

    if not allow_cold:
        contact = _lookup_contact_safely(contact_id=contact_id, phone=phone)
        if contact is None:
            raise SmsError(
                f'no HubSpot contact for {phone}; SMS is a warm-only channel, '
                'run email outreach first',
                reason='cold_contact_refused')
        props = contact.get('properties') or {}
        if str(props.get('sms_unsubscribed', '')).lower() in ('true', '1', 'yes'):
            raise SmsError(f'{phone} has sms_unsubscribed=true',
                           reason='unsubscribed')
        if not _is_warm(contact):
            raise SmsError(
                f'{phone} is not warm (hs_lead_status='
                f'{props.get("hs_lead_status") or "NEW"}); refusing cold SMS',
                reason='cold_contact_refused')

    destination, is_live = _resolve_sms_destination(phone)
    client = _get_sms_client()
    try:
        response = client.send(message, [destination], shortcode)
    except Exception as e:
        raise SmsError(f'AT send failed: {type(e).__name__}: {e}',
                       reason='provider_error') from e

    return {
        'status': 'sent',
        'destination': destination,
        'live_outbound': is_live,
        'requested_phone': phone,
        'provider_response': response,
    }


def _lookup_contact_safely(*, contact_id: str | None,
                           phone: str | None) -> dict | None:
    """Best-effort HubSpot lookup. Swallows import + network errors so a
    CRM outage can't silently turn the SMS channel into an unrouted
    broadcaster. On any failure the SMS path treats the contact as
    unknown (cold), which fails closed."""
    try:
        from hubspot_handler import get_contact, find_contact_by_phone
    except Exception as e:
        log.warning('sms: HubSpot import failed (%s); treating as cold', e)
        return None
    try:
        if contact_id:
            found = get_contact(contact_id)
            if found:
                return found
        if phone:
            return find_contact_by_phone(phone)
    except Exception as e:
        log.warning('sms: HubSpot lookup failed (%s); treating as cold', e)
    return None


def handle_inbound(payload: dict | None) -> dict:
    """Parse an AT inbound-SMS webhook, classify intent, and route the
    message downstream to HubSpot.

    Downstream side-effects (best-effort — a CRM outage does not fail
    the webhook):
      - STOP: mark the matched contact `sms_unsubscribed=true` and
              `hs_lead_status=UNQUALIFIED`; log an audit note.
      - BOOKING: warm the contact (`hs_lead_status=CONNECTED`) and log
                 the message so staff can follow up with a Cal.com link.
      - INFO / OTHER: warm the contact and log the message verbatim.

    Returns a dict the FastAPI route can act on:
      - action:      'stop' | 'booking' | 'info' | 'reply'
      - phone:       normalized E.164-ish phone string
      - text:        original message body
      - contact_id:  matched HubSpot contact id, or None
      - auto_reply:  suggested reply text, or None when we should stay silent
                     (STOP must be silent per AT policy)
    """
    if not isinstance(payload, dict):
        return {'action': 'ignored', 'phone': None, 'text': None,
                'contact_id': None, 'auto_reply': None,
                'error': f'payload is {type(payload).__name__}, expected dict'}

    phone = _normalize_phone(payload.get('from') or payload.get('phone'))
    text  = (payload.get('text') or '').strip()
    intent = _classify_intent(text)

    contact = _lookup_contact_safely(contact_id=None, phone=phone)
    contact_id = contact.get('id') if contact else None

    auto_reply: str | None = None
    if intent == 'stop':
        auto_reply = None  # AT / regulators require silent opt-out
        _apply_stop(contact_id, phone, text)
    elif intent == 'booking':
        auto_reply = ('Thanks — a Tenacious Research Partner will text a '
                      'booking link shortly.')
        _apply_warm(contact_id, phone, text, lead_status='CONNECTED',
                    note_prefix='[SMS inbound / booking intent]')
    elif intent == 'info':
        auto_reply = ('Happy to share more — reply BOOK to get a scheduling '
                      'link, or STOP to opt out.')
        _apply_warm(contact_id, phone, text, lead_status='CONNECTED',
                    note_prefix='[SMS inbound / info request]')
    else:
        _apply_warm(contact_id, phone, text, lead_status='CONNECTED',
                    note_prefix='[SMS inbound]')

    return {
        'action': intent if intent != 'other' else 'reply',
        'phone': phone,
        'text': text,
        'contact_id': contact_id,
        'auto_reply': auto_reply,
    }


def _apply_stop(contact_id: str | None, phone: str | None,
                text: str) -> None:
    """Record the opt-out on the matched contact. If no contact exists,
    log the fact for the audit sweep — we can't block a phone we've never
    seen, but we do want the opt-out visible to staff."""
    if not contact_id:
        log.info('sms STOP from unknown phone %s; nothing to update', phone)
        return
    try:
        from hubspot_handler import update_contact, log_note
        update_contact(contact_id, {
            'sms_unsubscribed': 'true',
            'hs_lead_status': 'UNQUALIFIED',
        })
        log_note(contact_id,
                 f'[SMS inbound / STOP] opt-out received from {phone}: {text!r}')
    except Exception as e:
        log.warning('sms: STOP propagation to HubSpot failed: %s', e)


def _apply_warm(contact_id: str | None, phone: str | None, text: str,
                *, lead_status: str, note_prefix: str) -> None:
    """Warm the contact in HubSpot and log the inbound message."""
    if not contact_id:
        log.info('sms inbound from unknown phone %s; skipping CRM write', phone)
        return
    try:
        from hubspot_handler import update_contact, log_note
        update_contact(contact_id, {'hs_lead_status': lead_status})
        log_note(contact_id, f'{note_prefix} from {phone}: {text!r}')
    except Exception as e:
        log.warning('sms: warm propagation to HubSpot failed: %s', e)
