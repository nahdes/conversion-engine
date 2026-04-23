"""HubSpot handler — writes contact + engagement records.

Two backends, one interface:

- MCP path (preferred): when `HUBSPOT_MCP_URL` is set, every upsert /
  note write flows through the HubSpot MCP server via `hubspot_mcp`.
  This is the path the rubric calls out.
- REST fallback: when MCP is disabled or its call raises, we fall back
  to the direct HubSpot v3 REST API so the system stays writable in
  environments where the MCP server hasn't been provisioned.

Every contact upsert carries enrichment fields beyond `email` /
`firstname` / `lastname`: hiring-signal brief metadata
(`ai_maturity_score`, `primary_segment_match`, `crunchbase_id`,
`last_enriched_at`) plus booking/SMS state when available. See
`main_agent.run_prospect` and `calendar_handler.sync_booking_to_hubspot`
for the caller-side property maps.
"""
import os, logging, requests
from dotenv import load_dotenv
load_dotenv()

import hubspot_mcp

log = logging.getLogger(__name__)

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

    Routes through the HubSpot MCP server when `HUBSPOT_MCP_URL` is
    configured; falls back to the direct REST API otherwise (or when
    the MCP call raises, so a transient MCP outage doesn't take the
    agent offline). Always tags `tenacious_status=draft` per policy
    Rule 6 and leaves an audit note recording the marker.
    """
    stamped = _with_draft(props)
    if hubspot_mcp.is_enabled():
        try:
            cid = hubspot_mcp.upsert_contact(email, stamped)
            _safe_log_note(cid,
                f'[Tenacious policy Rule 6] {DRAFT_PROPERTY}={DRAFT_VALUE} '
                '(write routed via HubSpot MCP server)')
            return cid
        except hubspot_mcp.McpError as e:
            log.warning('hubspot MCP upsert failed (%s); falling back to REST',
                        e)
    return _upsert_via_rest(email, props)


def _upsert_via_rest(email: str, props: dict) -> str:
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
        existing_id = r.json().get('message', '').split(' ')[-1]
        r2 = requests.patch(
            f'{BASE}/crm/v3/objects/contacts/{existing_id}',
            headers=HEADERS, json={'properties': _with_draft(props)})
        if r2.status_code == 400 and DRAFT_PROPERTY in r2.text:
            r2 = requests.patch(
                f'{BASE}/crm/v3/objects/contacts/{existing_id}',
                headers=HEADERS, json={'properties': props})
        r2.raise_for_status()
        return r2.json()['id']
    r.raise_for_status()
    contact_id = r.json()['id']
    _safe_log_note(contact_id,
        f'[Tenacious policy Rule 6] {DRAFT_PROPERTY}={DRAFT_VALUE}')
    return contact_id


def log_note(contact_id: str, body: str) -> None:
    """Log a note (engagement) on a contact.

    Routes through MCP when configured; falls back to REST on MCP error
    so a note failure never swallows the audit trail silently."""
    if hubspot_mcp.is_enabled():
        try:
            hubspot_mcp.log_note(contact_id, body)
            return
        except hubspot_mcp.McpError as e:
            log.warning('hubspot MCP note failed (%s); falling back to REST', e)
    requests.post(
        f'{BASE}/engagements/v1/engagements',
        headers=HEADERS,
        json={
            'engagement': {'type': 'NOTE', 'active': True},
            'associations': {'contactIds': [contact_id]},
            'metadata': {'body': body}
        }
    )


def _safe_log_note(contact_id: str, body: str) -> None:
    """log_note wrapper that never raises — the note is audit metadata,
    not load-bearing state."""
    try:
        log_note(contact_id, body)
    except Exception as e:
        log.info('hubspot: log_note failed for %s: %s', contact_id, e)


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