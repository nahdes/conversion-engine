import os, africastalking
from dotenv import load_dotenv
load_dotenv()

africastalking.initialize(
    os.environ['AT_USERNAME'],
    os.environ['AT_API_KEY']
)
sms = africastalking.SMS

STOP_WORDS = {'STOP', 'UNSUBSCRIBE', 'UNSUB', 'CANCEL', 'END'}

def send_sms(phone: str, message: str) -> dict:
    return sms.send(message, [phone], os.environ['AT_SHORTCODE'])

def handle_inbound(payload: dict) -> dict:
    text = payload.get('text', '').strip().upper()
    if text in STOP_WORDS:
        return {'action': 'stop', 'phone': payload['from']}
    return {'action': 'reply', 'text': payload.get('text'), 'phone': payload['from']}