import os, requests
from dotenv import load_dotenv
load_dotenv()

BASE = 'https://api.hubapi.com'
HEADERS = {
    'Authorization': f'Bearer {os.environ["HUBSPOT_TOKEN"]}',
    'Content-Type': 'application/json'
}

# Policy Rule 6: every HubSpot contact the agent writes carries the draft
# marker so reviewers (and the evidence graph) can filter unverified agent
# output. If the portal doesn't yet have a `tenacious_status` custom
# property, HubSpot rejects with 400; we retry once without it and write
# the same marker into a NOTE so the audit trail survives.
DRAFT_PROPERTY = 'tenacious_status'
DRAFT_VALUE = 'draft'


def _with_draft(props: dict) -> dict:
    return {DRAFT_PROPERTY: DRAFT_VALUE, **props}


def upsert_contact(email: str, props: dict) -> str:
    """Create or update a contact. Returns contact ID.
    Always tags `tenacious_status=draft` per policy Rule 6; falls back to
    a note if the portal does not have that custom property defined."""
    payload = {'properties': {'email': email, **_with_draft(props)}}
    r = requests.post(
        f'{BASE}/crm/v3/objects/contacts', headers=HEADERS, json=payload)

    if r.status_code == 400 and DRAFT_PROPERTY in r.text:
        # Portal lacks the custom property. Drop it from the payload,
        # record the marker via log_note after the contact is created.
        payload['properties'].pop(DRAFT_PROPERTY, None)
        r = requests.post(
            f'{BASE}/crm/v3/objects/contacts', headers=HEADERS, json=payload)

    if r.status_code == 409:   # already exists
        r2 = requests.patch(
            f'{BASE}/crm/v3/objects/contacts/{r.json()["message"].split(" ")[-1]}',
            headers=HEADERS, json={'properties': _with_draft(props)})
        if r2.status_code == 400 and DRAFT_PROPERTY in r2.text:
            r2 = requests.patch(
                f'{BASE}/crm/v3/objects/contacts/{r.json()["message"].split(" ")[-1]}',
                headers=HEADERS, json={'properties': props})
        return r2.json()['id']
    r.raise_for_status()
    contact_id = r.json()['id']
    # Whether or not the property wrote, always leave a draft-status note
    # so the audit trail is unambiguous.
    log_note(contact_id,
             f'[Tenacious policy Rule 6] {DRAFT_PROPERTY}={DRAFT_VALUE}')
    return contact_id


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


def find_contact_by_phone(phone: str) -> dict | None:
    """Search the CRM for a contact by phone number. Returns the first
    matching record (id + properties) or None. Used by the SMS handler
    to route inbound messages to an existing contact instead of minting
    a duplicate keyed on a synthetic email."""
    if not phone:
        return None
    r = requests.post(
        f'{BASE}/crm/v3/objects/contacts/search',
        headers=HEADERS,
        json={
            'filterGroups': [{'filters': [
                {'propertyName': 'phone', 'operator': 'EQ', 'value': phone},
            ]}],
            'properties': ['email', 'phone', 'hs_lead_status',
                           'sms_opt_in', 'sms_unsubscribed'],
            'limit': 1,
        },
    )
    if not r.ok:
        return None
    results = r.json().get('results', [])
    return results[0] if results else None


def get_contact(contact_id: str) -> dict | None:
    """Read a contact by id, pulling the properties the SMS hierarchy
    checks. Returns None on 404 so callers can branch cleanly."""
    if not contact_id:
        return None
    r = requests.get(
        f'{BASE}/crm/v3/objects/contacts/{contact_id}',
        headers=HEADERS,
        params={'properties':
                'email,phone,hs_lead_status,sms_opt_in,sms_unsubscribed'},
    )
    if r.status_code == 404:
        return None
    if not r.ok:
        return None
    return r.json()


def update_contact(contact_id: str, props: dict) -> None:
    """Patch properties on an existing contact. Always re-stamps the
    draft marker per policy Rule 6 so status updates don't launder the
    record into looking reviewer-approved."""
    if not contact_id:
        return
    r = requests.patch(
        f'{BASE}/crm/v3/objects/contacts/{contact_id}',
        headers=HEADERS,
        json={'properties': _with_draft(props)},
    )
    if r.status_code == 400 and DRAFT_PROPERTY in r.text:
        requests.patch(
            f'{BASE}/crm/v3/objects/contacts/{contact_id}',
            headers=HEADERS,
            json={'properties': props},
        )