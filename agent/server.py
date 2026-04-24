"""FastAPI webhook receiver.

Every inbound event runs through the centralized `ChannelRouter` so the
state transition and the matching HubSpot writes happen in one place —
not scattered across `email_handler`, `sms_handler`, and `calendar_handler`.
"""
import logging
from fastapi import FastAPI, Request
import uvicorn

from sms_handler import handle_inbound, send_sms, SmsError
from email_handler import handle_reply_webhook
from calendar_handler import handle_booking_webhook
from channel_router import (ChannelRouter, ChannelTransitionError,
                            load_router, compose_booking_link)
import hubspot_handler

log = logging.getLogger(__name__)

app = FastAPI()


def _router_for_email(email_addr: str | None):
    """Resolve the ChannelRouter keyed to an email address. We look up
    the contact via HubSpot search; if none exists, no router is built
    (the webhook still returns 200 so the provider doesn't retry).
    Centralized here so both bounce and reply paths hydrate identically.
    """
    if not email_addr:
        return None
    try:
        import requests, os
        r = requests.post(
            'https://api.hubapi.com/crm/v3/objects/contacts/search',
            headers={'Authorization':
                     f'Bearer {os.environ["HUBSPOT_TOKEN"]}',
                     'Content-Type': 'application/json'},
            json={'filterGroups': [{'filters': [
                {'propertyName': 'email', 'operator': 'EQ',
                 'value': email_addr}]}],
                  'properties': ['email', 'hs_lead_status',
                                 'tenacious_channel_state'],
                  'limit': 1},
            timeout=10)
        if r.ok:
            results = r.json().get('results') or []
            if results:
                cid = results[0]['id']
                props = results[0].get('properties') or {}
                return ChannelRouter(cid, props)
    except Exception as e:
        log.warning('server: hubspot lookup for %s failed: %s', email_addr, e)
    return None


@app.post('/webhook/email')
async def email_webhook(req: Request):
    """Resend email webhook. Parses + classifies, then the router
    advances state (EMAIL_REPLIED / BOUNCED / OPTED_OUT). For a reply,
    we attach a canonical Cal.com booking link so the response that
    goes back to the prospect (composed downstream) can reference it."""
    payload = await req.json()
    parsed = handle_reply_webhook(payload)
    log.info('email webhook: %s', parsed)

    router_state = None
    booking_link = None
    if parsed.get('category') == 'replied' and parsed.get('from_email'):
        router = _router_for_email(parsed['from_email'])
        if router and router.can_receive_email():
            try:
                router.on_email_reply(
                    from_email=parsed['from_email'],
                    subject=parsed.get('subject'),
                    message_id=parsed.get('message_id'))
                booking_link = router.booking_link_for('email')
                router_state = router.state.value
            except ChannelTransitionError as e:
                log.warning('email reply transition refused: %s', e)
    elif parsed.get('category') == 'bounced' and parsed.get('to_email'):
        router = _router_for_email(parsed['to_email'])
        if router:
            try:
                router.on_email_bounce(
                    to_email=parsed['to_email'],
                    reason=parsed.get('reason'),
                    message_id=parsed.get('message_id'))
                router_state = router.state.value
            except ChannelTransitionError as e:
                log.warning('email bounce transition refused: %s', e)
    elif parsed.get('category') == 'complained' and parsed.get('from_email'):
        router = _router_for_email(parsed['from_email'])
        if router:
            try:
                router.on_opt_out(channel='email',
                                  reason='spam complaint via Resend webhook')
                router_state = router.state.value
            except ChannelTransitionError as e:
                log.warning('email complaint transition refused: %s', e)

    return {'status': 'ok', 'parsed': parsed,
            'router_state': router_state,
            'booking_link': booking_link}


@app.post('/webhook/sms')
async def sms_webhook(req: Request):
    """Inbound SMS route.

    `handle_inbound` parses + classifies + mirrors to HubSpot. Then the
    router advances state centrally (the old handler wrote CRM state
    directly; now every state change flows through the router). On a
    booking intent the router issues the canonical Cal.com link via
    `compose_booking_link` so the link the SMS handler forwards matches
    the one the email path sends."""
    payload = await req.json()
    parsed = handle_inbound(payload)
    log.info('sms inbound: %s', parsed)

    router_state = None
    auto_reply = parsed.get('auto_reply')
    contact_id = parsed.get('contact_id')

    if contact_id:
        router = load_router(contact_id)
        try:
            router.on_sms_inbound(phone=parsed.get('phone') or '',
                                  text=parsed.get('text') or '',
                                  intent=parsed.get('action') or 'other')
            router_state = router.state.value
            # Attach a canonical booking link to the auto-reply so the
            # SMS path references the same calendar URL as the email path.
            if (parsed.get('action') in ('booking', 'info')
                    and auto_reply and router.can_send_sms()):
                auto_reply = f'{auto_reply} {router.booking_link_for("sms")}'
        except ChannelTransitionError as e:
            log.warning('sms inbound transition refused: %s', e)

    reply_status = None
    if auto_reply and parsed.get('phone'):
        try:
            reply_status = send_sms(
                parsed['phone'], auto_reply,
                contact_id=contact_id)
        except SmsError as e:
            log.info('sms auto-reply skipped: %s (%s)', e, e.reason)
            reply_status = {'status': 'skipped', 'reason': e.reason}

    return {'status': 'ok', 'parsed': parsed,
            'router_state': router_state,
            'reply': reply_status}


@app.post('/webhook/calcom')
async def calcom_webhook(req: Request):
    """Cal.com booking webhook. The booking creates / updates the
    HubSpot contact inside `handle_booking_webhook`; we then advance the
    router to BOOKED so `tenacious_channel_state` matches the
    `hs_lead_status=OPEN_DEAL` write."""
    payload = await req.json()
    parsed = handle_booking_webhook(payload)
    log.info('cal.com booking: %s', parsed)

    router_state = None
    if parsed.get('contact_id'):
        router = load_router(parsed['contact_id'])
        try:
            # Pull booking metadata from the raw payload so the router
            # records uid + start_time + meeting_url.
            data = (payload.get('payload')
                    if isinstance(payload.get('payload'), dict) else payload)
            router.on_booking_created(
                booking_uid=parsed.get('booking_uid') or '',
                start_time=(data.get('startTime') or data.get('start') or ''),
                meeting_url=data.get('meetingUrl')
                    or (data.get('metadata') or {}).get('videoCallUrl'))
            router_state = router.state.value
        except ChannelTransitionError as e:
            log.warning('booking transition refused: %s', e)

    return {'status': 'ok', **parsed, 'router_state': router_state}


if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8000)
