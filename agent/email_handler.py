import os, resend
from dotenv import load_dotenv
load_dotenv()
resend.api_key = os.environ['RESEND_API_KEY']

# Policy Rule 6: every Tenacious-branded outbound carries the draft marker
# as a custom header so downstream systems (review dashboards, mail clients,
# audit logs) can filter unverified agent output.
DRAFT_HEADER_NAME = 'X-Tenacious-Status'
DRAFT_HEADER_VALUE = 'draft'


def send_outreach(to_email: str, subject: str, html_body: str) -> str:
    r = resend.Emails.send({
        'from': os.environ['FROM_EMAIL'],
        'to': [to_email],
        'subject': subject,
        'html': html_body,
        'headers': {DRAFT_HEADER_NAME: DRAFT_HEADER_VALUE},
    })
    return r['id']


def handle_reply_webhook(payload: dict) -> dict:
    """Parse Resend reply webhook and extract lead intent."""
    return {
        'from_email': payload.get('from', {}).get('email'),
        'subject': payload.get('subject'),
        'text': payload.get('text', '')
    }