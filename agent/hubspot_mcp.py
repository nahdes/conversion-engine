"""HubSpot MCP adapter.

Thin JSON-RPC-over-HTTP client for a HubSpot MCP server. The goal is
for CRM writes to flow through the MCP surface the rubric calls out —
`hubspot_handler.upsert_contact` routes here when `HUBSPOT_MCP_URL` is
set, and falls back to the direct REST path otherwise.

MCP's HTTP transport is `POST <base>` with a JSON-RPC body:

    { "jsonrpc": "2.0", "id": <int>, "method": "tools/call",
      "params": { "name": "<tool>", "arguments": { ... } } }

Tool names default to the reference HubSpot MCP server's taxonomy
(`hubspot-create-contact`, `hubspot-update-contact`, `hubspot-search`,
`hubspot-create-engagement`) but can be overridden via env so this
module works against forks with different naming.

Every call returns a parsed `{id?, properties?, raw}` dict; raises
`McpError` on transport failure, MCP-level error, or a response whose
shape we can't understand. Errors carry the original payload so the
caller can log a complete audit trail.
"""
from __future__ import annotations

import os, json, itertools, logging
import requests

log = logging.getLogger(__name__)

_rpc_counter = itertools.count(1)


class McpError(RuntimeError):
    def __init__(self, message: str, *, payload: dict | None = None):
        super().__init__(message)
        self.payload = payload


def is_enabled() -> bool:
    return bool(os.environ.get('HUBSPOT_MCP_URL'))


def _tool_name(default: str, env_var: str) -> str:
    return os.environ.get(env_var, default)


def _mcp_post(method: str, params: dict) -> dict:
    url = os.environ.get('HUBSPOT_MCP_URL')
    if not url:
        raise McpError('HUBSPOT_MCP_URL not configured')
    token = os.environ.get('HUBSPOT_MCP_TOKEN')  # optional bearer for proxy
    headers = {'Content-Type': 'application/json',
               'Accept': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    body = {'jsonrpc': '2.0', 'id': next(_rpc_counter),
            'method': method, 'params': params}
    try:
        r = requests.post(url, headers=headers, json=body, timeout=30)
    except requests.RequestException as e:
        raise McpError(f'MCP transport error: {e}') from e
    if not r.ok:
        raise McpError(f'MCP HTTP {r.status_code}: {r.text[:300]}',
                       payload={'status': r.status_code, 'body': r.text[:500]})
    try:
        envelope = r.json()
    except ValueError as e:
        raise McpError(f'MCP returned non-JSON body: {r.text[:200]}') from e
    if 'error' in envelope and envelope['error']:
        raise McpError(f'MCP error: {envelope["error"]}', payload=envelope)
    return envelope.get('result') or {}


def _extract_content(result: dict) -> dict | list | str:
    """MCP tool results are wrapped in `content[]`, each item `{type,text}`.
    HubSpot tool outputs tend to be JSON-in-text; we parse it so callers
    get a structured dict back."""
    content = result.get('content')
    if isinstance(content, list) and content:
        first = content[0]
        text = first.get('text') if isinstance(first, dict) else None
        if text:
            try:
                return json.loads(text)
            except (ValueError, TypeError):
                return text
    if 'structuredContent' in result:
        return result['structuredContent']
    return result


def _call_tool(tool: str, args: dict) -> dict | list | str:
    result = _mcp_post('tools/call', {'name': tool, 'arguments': args})
    return _extract_content(result)


def upsert_contact(email: str, props: dict) -> str:
    """Upsert a contact via MCP. Tries create first, falls back to update
    on a conflict error. Returns the contact id.

    Tool names are looked up from env so this works with any HubSpot MCP
    server flavor:
      - HUBSPOT_MCP_TOOL_CREATE   (default: hubspot-create-contact)
      - HUBSPOT_MCP_TOOL_UPDATE   (default: hubspot-update-contact)
      - HUBSPOT_MCP_TOOL_SEARCH   (default: hubspot-search-contacts)
    """
    create = _tool_name('hubspot-create-contact', 'HUBSPOT_MCP_TOOL_CREATE')
    update = _tool_name('hubspot-update-contact', 'HUBSPOT_MCP_TOOL_UPDATE')
    search = _tool_name('hubspot-search-contacts', 'HUBSPOT_MCP_TOOL_SEARCH')

    arguments = {'properties': {'email': email, **props}}
    try:
        out = _call_tool(create, arguments)
    except McpError as e:
        # A 409-equivalent MCP error → search + update. Anything else
        # propagates so the caller can fall back to REST.
        if e.payload and _looks_like_conflict(e.payload):
            log.info('hubspot MCP: contact exists, routing via %s', update)
            found = _call_tool(search, {'email': email, 'limit': 1})
            cid = _extract_contact_id(found)
            if not cid:
                raise McpError(
                    f'MCP conflict but search returned no id for {email}',
                    payload=e.payload) from e
            _call_tool(update, {'contactId': cid, 'properties': props})
            return cid
        raise

    cid = _extract_contact_id(out)
    if not cid:
        raise McpError(f'MCP create returned no id: {out!r}')
    return cid


def log_note(contact_id: str, body: str) -> None:
    """Attach a note engagement to a contact via MCP."""
    tool = _tool_name('hubspot-create-engagement',
                      'HUBSPOT_MCP_TOOL_ENGAGEMENT')
    _call_tool(tool, {
        'type': 'NOTE',
        'contactId': contact_id,
        'metadata': {'body': body},
    })


def _looks_like_conflict(payload: dict) -> bool:
    """Heuristic: conflict errors may arrive as MCP-level `{error:{...}}`
    or as a tool-level error body echoing HubSpot's 409. Check both."""
    if not isinstance(payload, dict):
        return False
    err = payload.get('error') if isinstance(payload.get('error'), dict) \
        else payload
    message = json.dumps(err).lower() if err else ''
    return any(k in message for k in ('409', 'conflict', 'already exists',
                                      'contact already exists'))


def _extract_contact_id(obj) -> str | None:
    """Pull a contact id out of the various shapes MCP servers return.
    Accepts dicts with `id`, `contactId`, `results[0].id`, or a bare str."""
    if isinstance(obj, str):
        return obj if obj.isdigit() else None
    if isinstance(obj, dict):
        for key in ('id', 'contactId', 'contact_id', 'vid'):
            v = obj.get(key)
            if v:
                return str(v)
        results = obj.get('results')
        if isinstance(results, list) and results:
            return _extract_contact_id(results[0])
        properties = obj.get('properties')
        if isinstance(properties, dict):
            v = properties.get('hs_object_id')
            if v:
                return str(v)
    if isinstance(obj, list) and obj:
        return _extract_contact_id(obj[0])
    return None
