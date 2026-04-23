import logging
from fastapi import FastAPI, Request
import uvicorn
from sms_handler import handle_inbound, send_sms, SmsError
from email_handler import handle_reply_webhook
from calendar_handler import handle_booking_webhook

log = logging.getLogger(__name__)

app = FastAPI()

@app.post('/webhook/email')
async def email_webhook(req: Request):
    payload = await req.json()
    parsed = handle_reply_webhook(payload)
    print('Email reply:', parsed)
    return {'status': 'ok'}

@app.post('/webhook/sms')
async def sms_webhook(req: Request):
    """Inbound SMS route.

    `handle_inbound` parses, classifies intent, and mirrors the message
    to HubSpot (warming the contact or recording STOP). The webhook
    layer then acts on the structured result: if the handler suggests
    an `auto_reply`, we send it back through `send_sms`, which in turn
    enforces the same warm-channel hierarchy and kill-switch as outbound
    prospect SMS (so an auto-reply to an unknown or unsubscribed phone
    cannot leak out)."""
    payload = await req.json()
    parsed = handle_inbound(payload)
    print('SMS inbound:', parsed)

    reply_status = None
    if parsed.get('auto_reply') and parsed.get('phone'):
        try:
            reply_status = send_sms(
                parsed['phone'], parsed['auto_reply'],
                contact_id=parsed.get('contact_id'))
        except SmsError as e:
            log.info('sms auto-reply skipped: %s (%s)', e, e.reason)
            reply_status = {'status': 'skipped', 'reason': e.reason}

    return {'status': 'ok', 'parsed': parsed, 'reply': reply_status}

@app.post('/webhook/calcom')
async def calcom_webhook(req: Request):
    payload = await req.json()
    parsed = handle_booking_webhook(payload)
    print('Cal.com booking:', parsed)
    return {'status': 'ok', **parsed}

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8000)
