import os, requests
from dotenv import load_dotenv
load_dotenv()

BASE = 'https://api.hubapi.com'
HEADERS = {
    'Authorization': f'Bearer {os.environ["HUBSPOT_TOKEN"]}',
    'Content-Type': 'application/json'
}

def upsert_contact(email: str, props: dict) -> str:
    """Create or update a contact. Returns contact ID."""
    r = requests.post(
        f'{BASE}/crm/v3/objects/contacts',
        headers=HEADERS,
        json={'properties': {'email': email, **props}}
    )
    if r.status_code == 409:   # already exists
        r2 = requests.patch(
            f'{BASE}/crm/v3/objects/contacts/{r.json()["message"].split(" ")[-1]}',
            headers=HEADERS, json={'properties': props}
        )
        return r2.json()['id']
    r.raise_for_status()
    return r.json()['id']

def log_note(contact_id: str, body: str) -> None:
    """Log a note (engagement) on a contact."""
    requests.post(
        f'{BASE}/engagements/v1/engagements',
        headers=HEADERS,
        json={
            'engagement': {'type': 'NOTE', 'active': True},
            'associations': {'contactIds': [contact_id]},
            'metadata': {'body': body}
        }
    )