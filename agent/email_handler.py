"""Email handler — Resend send + reply-webhook parsing.

Failure model:
- Transient (429, 5xx, connection reset): retry with capped exponential
  backoff, respect `Retry-After` when the server supplies it.
- Permanent (4xx other than 429): raise EmailSendError with the server's
  message so callers can log and fail the run cleanly.
- Input validation happens before the first HTTP call so a bad address
  never eats a retry budget.

Policy Rule 6: every outbound carries the draft marker as a custom
header so downstream systems (review dashboards, audit logs) can filter
unverified agent output.
"""
from __future__ import annotations

import os, re, time, logging, random
from typing import Any
import requests
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

DRAFT_HEADER_NAME = 'X-Tenacious-Status'
DRAFT_HEADER_VALUE = 'draft'

RESEND_ENDPOINT = 'https://api.resend.com/emails'
HTTP_TIMEOUT = 15
MAX_ATTEMPTS = 4
BACKOFF_BASE = 1.0
BACKOFF_CAP = 15.0
MAX_BODY_BYTES = 500_000

# Permissive RFC-5322-ish check — rejects the obvious bad cases (spaces,
# missing @, missing TLD) without pulling a full parser.
_EMAIL_RE = re.compile(r'^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$')


class EmailSendError(RuntimeError):
    """Send failed permanently. Carries the status code and server body
    so the caller can log a useful trace without re-inspecting the
    requests.Response."""

    def __init__(self, message: str, *, status: int | None = None,
                 body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


def _validate_email(addr: str, field: str) -> None:
    if not addr or not isinstance(addr, str):
        raise EmailSendError(f'{field} missing')
    if not _EMAIL_RE.match(addr.strip()):
        raise EmailSendError(f'{field} is not a valid address: {addr!r}')


def _sleep_with_retry_after(resp: requests.Response, attempt: int) -> None:
    """Prefer server-supplied Retry-After, fall back to jittered
    exponential backoff. `attempt` is 1-indexed."""
    retry_after = resp.headers.get('Retry-After') if resp is not None else None
    delay: float
    if retry_after:
        try:
            delay = float(retry_after)
        except ValueError:
            delay = BACKOFF_BASE * (2 ** (attempt - 1))
    else:
        delay = BACKOFF_BASE * (2 ** (attempt - 1))
    delay = min(delay, BACKOFF_CAP)
    delay += random.uniform(0, 0.5)
    time.sleep(delay)


def send_outreach(to_email: str, subject: str, html_body: str,
                  *, idempotency_key: str | None = None) -> str:
    """Send an outreach email. Returns the Resend message id.

    Raises EmailSendError on permanent failure (bad input, 4xx, or
    exhausted retries on 5xx). Transient failures (429, 5xx, connection
    reset) are retried up to MAX_ATTEMPTS with backoff.

    `idempotency_key` is forwarded to Resend via the Idempotency-Key
    header so a retried send won't duplicate the message on the provider
    side. Pass a stable value (e.g. prospect_domain + run_id) when you
    want that guarantee.
    """
    api_key = os.environ.get('RESEND_API_KEY')
    from_email = os.environ.get('FROM_EMAIL')
    if not api_key:
        raise EmailSendError('RESEND_API_KEY not configured')
    if not from_email:
        raise EmailSendError('FROM_EMAIL not configured')
    _validate_email(to_email, 'to_email')
    _validate_email(from_email, 'FROM_EMAIL')
    if not subject or not subject.strip():
        raise EmailSendError('subject must be non-empty')
    if not html_body or not html_body.strip():
        raise EmailSendError('html_body must be non-empty')
    if len(html_body.encode('utf-8')) > MAX_BODY_BYTES:
        raise EmailSendError(
            f'html_body exceeds {MAX_BODY_BYTES} bytes after utf-8 encoding')

    body: dict[str, Any] = {
        'from': from_email,
        'to': [to_email],
        'subject': subject.strip(),
        'html': html_body,
        'headers': {DRAFT_HEADER_NAME: DRAFT_HEADER_VALUE},
    }
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    if idempotency_key:
        headers['Idempotency-Key'] = idempotency_key

    last_err: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.post(
                RESEND_ENDPOINT, headers=headers, json=body,
                timeout=HTTP_TIMEOUT)
        except (requests.Timeout, requests.ConnectionError) as e:
            last_err = e
            log.warning('resend: transport error on attempt %d/%d: %s',
                        attempt, MAX_ATTEMPTS, e)
            if attempt == MAX_ATTEMPTS:
                break
            time.sleep(min(BACKOFF_CAP, BACKOFF_BASE * (2 ** (attempt - 1))))
            continue

        if resp.status_code == 200 or resp.status_code == 201:
            try:
                return resp.json()['id']
            except (ValueError, KeyError) as e:
                raise EmailSendError(
                    f'resend returned {resp.status_code} with unexpected body',
                    status=resp.status_code, body=resp.text[:500]) from e

        # 429 and 5xx are retryable.
        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            log.warning('resend: retryable HTTP %d on attempt %d/%d: %s',
                        resp.status_code, attempt, MAX_ATTEMPTS,
                        resp.text[:200])
            last_err = EmailSendError(
                f'resend HTTP {resp.status_code}',
                status=resp.status_code, body=resp.text[:500])
            if attempt == MAX_ATTEMPTS:
                break
            _sleep_with_retry_after(resp, attempt)
            continue

        # Permanent 4xx — don't retry.
        raise EmailSendError(
            f'resend rejected: HTTP {resp.status_code}: {resp.text[:300]}',
            status=resp.status_code, body=resp.text[:500])

    # Exhausted retries.
    if isinstance(last_err, EmailSendError):
        raise last_err
    raise EmailSendError(
        f'resend send failed after {MAX_ATTEMPTS} attempts: {last_err}')


# Resend webhook event taxonomy. Every event lands in exactly one bucket
# so downstream routing never has to string-match the event name:
#   - replied:  a prospect responded, run the warm-lead flow
#   - bounced:  the destination is invalid / blocked — mark hardfail
#   - complained: the recipient flagged the message as spam
#   - delayed:  provider is retrying; no action needed, just audit
#   - delivered / sent / opened / clicked: delivery-telemetry noise
#   - malformed: payload couldn't be parsed
#   - unknown:  event type we don't recognize (log, don't act)
_REPLY_EVENTS     = {'email.replied', 'email.reply.received',
                     'email.inbound', 'inbound.email'}
_BOUNCE_EVENTS    = {'email.bounced', 'email.bounce',
                     'email.delivery.failed', 'email.failed'}
_COMPLAINT_EVENTS = {'email.complained', 'email.complaint',
                     'email.marked_as_spam'}
_DELAY_EVENTS     = {'email.delivery_delayed', 'email.deferred'}
_DELIVERY_EVENTS  = {'email.delivered', 'email.sent',
                     'email.opened', 'email.clicked'}


def _classify_event(event_type: str | None) -> str:
    if not event_type:
        return 'unknown'
    e = str(event_type).lower()
    if e in _REPLY_EVENTS:
        return 'replied'
    if e in _BOUNCE_EVENTS:
        return 'bounced'
    if e in _COMPLAINT_EVENTS:
        return 'complained'
    if e in _DELAY_EVENTS:
        return 'delayed'
    if e in _DELIVERY_EVENTS:
        return 'delivery'
    return 'unknown'


def handle_reply_webhook(payload: dict | None) -> dict:
    """Parse a Resend webhook into a downstream-friendly shape.

    Resend has shipped three payload shapes over time (legacy inbound,
    typed event-with-data, bare event dict). All three are handled here
    so a schema drift doesn't silently turn bounces into replies.

    Returns a dict with:
      - event:      raw event type string (e.g. 'email.bounced')
      - category:   one of 'replied' | 'bounced' | 'complained' |
                    'delayed' | 'delivery' | 'malformed' | 'unknown'
      - from_email: sender address (reply) or recipient address (bounce),
                    or None if the payload didn't carry one
      - to_email:   the original recipient for bounce / complaint events
      - subject:    subject line or None
      - text:       best-effort plain-text body; empty on non-reply events
      - message_id: Resend id when present, for audit-trail correlation
      - reason:     bounce reason / diagnostic code when present
      - error:      diagnostic string when `category == 'malformed'`

    Malformed payloads never raise — the webhook caller always gets a
    categorized result so it can log and return 200 to Resend (non-200
    responses cause Resend to retry the event indefinitely).
    """
    if not isinstance(payload, dict):
        return {'event': None, 'category': 'malformed',
                'from_email': None, 'to_email': None,
                'subject': None, 'text': '',
                'message_id': None, 'reason': None,
                'error': f'payload is {type(payload).__name__}, expected dict'}

    event_type = payload.get('type') or payload.get('event')
    category = _classify_event(event_type)
    data = payload.get('data') if isinstance(payload.get('data'), dict) \
        else payload

    sender = data.get('from')
    if isinstance(sender, dict):
        from_email = sender.get('email')
    elif isinstance(sender, str):
        from_email = sender
    else:
        from_email = None
    if from_email and not _EMAIL_RE.match(from_email.strip()):
        log.info('resend webhook: dropping malformed from_email=%r', from_email)
        from_email = None

    # Recipient lives under `to` for bounces; can be list or string.
    to_raw = data.get('to') or data.get('recipient')
    if isinstance(to_raw, list) and to_raw:
        to_raw = to_raw[0]
    if isinstance(to_raw, dict):
        to_email = to_raw.get('email')
    elif isinstance(to_raw, str):
        to_email = to_raw
    else:
        to_email = None
    if to_email and not _EMAIL_RE.match(to_email.strip()):
        to_email = None

    reason = (data.get('reason') or data.get('bounce_reason')
              or data.get('diagnostic_code'))

    # For non-reply events we deliberately don't surface body text — a
    # bounce payload sometimes echoes the original subject/body and
    # downstream code should not treat that as a customer reply.
    text = (data.get('text') or data.get('html') or '') \
        if category == 'replied' else ''
    subject = data.get('subject') if category in ('replied', 'bounced') else None

    out = {
        'event': event_type,
        'category': category,
        'from_email': from_email.strip() if from_email else None,
        'to_email': to_email.strip() if to_email else None,
        'subject': subject,
        'text': text,
        'message_id': data.get('email_id') or data.get('id'),
        'reason': reason,
    }
    if category == 'unknown':
        log.info('resend webhook: unknown event_type=%r', event_type)
    elif category in ('bounced', 'complained'):
        log.warning('resend webhook: %s event for %s (reason=%s)',
                    category, out['to_email'] or out['from_email'], reason)
    return out
