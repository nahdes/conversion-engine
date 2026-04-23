from fastapi import FastAPI, Request
import uvicorn
from sms_handler import handle_inbound
from email_handler import handle_reply_webhook
from calendar_handler import handle_booking_webhook

app = FastAPI()

@app.post('/webhook/email')
async def email_webhook(req: Request):
    payload = await req.json()
    parsed = handle_reply_webhook(payload)
    print('Email reply:', parsed)
    return {'status': 'ok'}

@app.post('/webhook/sms')
async def sms_webhook(req: Request):
    payload = await req.json()
    parsed = handle_inbound(payload)
    print('SMS inbound:', parsed)
    return {'status': 'ok'}

@app.post('/webhook/calcom')
async def calcom_webhook(req: Request):
    payload = await req.json()
    parsed = handle_booking_webhook(payload)
    print('Cal.com booking:', parsed)
    return {'status': 'ok', **parsed}

if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8000)
