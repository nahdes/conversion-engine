"""Connectivity probe for the four external integrations.

Each check makes a minimal read-only auth call and reports PASS / FAIL.
Nothing is sent to real recipients unless --live-send is passed.

Usage:
    python agent/check_integrations.py              # read-only checks
    python agent/check_integrations.py --live-send  # also send to staff sinks
"""
from __future__ import annotations

import argparse, io, os, sys
from dotenv import load_dotenv
import requests

# Force UTF-8 on Windows consoles so non-ASCII in API responses doesn't crash.
if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                                  errors='replace')

load_dotenv()

OK, FAIL, MISS = 'PASS', 'FAIL', 'MISSING'
TIMEOUT = 10


def _missing(*keys) -> list[str]:
    return [k for k in keys if not os.environ.get(k)]


def check_calcom() -> tuple[str, str]:
    missing = _missing('CALCOM_API_KEY')
    if missing:
        return MISS, f'env not set: {", ".join(missing)}'
    key = os.environ['CALCOM_API_KEY']
    # v2 is the only supported API (v1 was decommissioned). Bearer auth.
    try:
        r = requests.get(
            'https://api.cal.com/v2/me',
            headers={
                'Authorization': f'Bearer {key}',
                'cal-api-version': '2024-08-13',
            },
            timeout=TIMEOUT,
        )
    except Exception as e:
        return FAIL, f'network: {type(e).__name__}: {e}'
    if r.status_code == 200:
        data = r.json().get('data') or r.json()
        email = data.get('email') or data.get('username') or '(no email)'
        event_type = os.environ.get('CALCOM_EVENT_TYPE_ID', '(unset)')
        return OK, f'v2 authenticated as {email}; event_type_id={event_type}'
    if r.status_code in (401, 403):
        return FAIL, (f'v2 auth rejected HTTP {r.status_code}. '
                      'Generate a v2 key at cal.com → Settings → Developer → '
                      'API Keys (v2).')
    return FAIL, f'HTTP {r.status_code}: {r.text[:200]}'


def check_hubspot() -> tuple[str, str]:
    missing = _missing('HUBSPOT_TOKEN')
    if missing:
        return MISS, f'env not set: {", ".join(missing)}'
    token = os.environ['HUBSPOT_TOKEN']
    try:
        r = requests.get(
            'https://api.hubapi.com/crm/v3/objects/contacts',
            headers={'Authorization': f'Bearer {token}'},
            params={'limit': 1},
            timeout=TIMEOUT,
        )
    except Exception as e:
        return FAIL, f'network: {type(e).__name__}: {e}'
    if r.status_code == 200:
        n = len(r.json().get('results', []))
        return OK, f'read contacts OK (returned {n} row)'
    if r.status_code in (401, 403):
        # Surface the server's error message — HubSpot returns the missing
        # scope name when the token lacks permission.
        body = r.text[:300]
        hint = ('If the message mentions scopes, edit the private app in '
                'HubSpot (Settings -> Integrations -> Private Apps) and add '
                'crm.objects.contacts.read and crm.objects.contacts.write.')
        return FAIL, f'HTTP {r.status_code}: {body} | {hint}'
    return FAIL, f'HTTP {r.status_code}: {r.text[:200]}'


def check_africastalking() -> tuple[str, str]:
    missing = _missing('AT_USERNAME', 'AT_API_KEY')
    if missing:
        return MISS, f'env not set: {", ".join(missing)}'
    user = os.environ['AT_USERNAME']
    key = os.environ['AT_API_KEY']
    host = ('https://api.sandbox.africastalking.com' if user == 'sandbox'
            else 'https://api.africastalking.com')
    # Skip the SDK and hit the HTTP endpoint directly — on Windows the SDK
    # sometimes picks an old TLS handshake that fails with WRONG_VERSION_NUMBER.
    try:
        r = requests.get(
            f'{host}/version1/user',
            params={'username': user},
            headers={'apiKey': key, 'Accept': 'application/json'},
            timeout=TIMEOUT,
        )
    except requests.exceptions.SSLError as e:
        return FAIL, (f'SSL handshake failed: {e}. '
                      'If on a corporate/proxy network, try a direct '
                      'connection; otherwise upgrade certifi.')
    except Exception as e:
        return FAIL, f'network: {type(e).__name__}: {e}'
    if r.status_code == 200:
        try:
            balance = r.json()['UserData']['balance']
        except Exception:
            balance = '(no balance field)'
        shortcode = os.environ.get('AT_SHORTCODE', '(unset)')
        return OK, f'balance={balance}; shortcode={shortcode}; host={host}'
    if r.status_code in (401, 403):
        return FAIL, (f'auth rejected HTTP {r.status_code}. '
                      'Check AT_USERNAME / AT_API_KEY match the dashboard.')
    return FAIL, f'HTTP {r.status_code}: {r.text[:200]}'


def check_resend() -> tuple[str, str]:
    missing = _missing('RESEND_API_KEY')
    if missing:
        return MISS, f'env not set: {", ".join(missing)}'
    try:
        r = requests.get(
            'https://api.resend.com/domains',
            headers={'Authorization': f'Bearer {os.environ["RESEND_API_KEY"]}'},
            timeout=TIMEOUT,
        )
    except Exception as e:
        return FAIL, f'network: {type(e).__name__}: {e}'
    if r.status_code == 200:
        domains = r.json().get('data', []) or []
        names = ', '.join(d.get('name', '?') for d in domains) or '(none yet)'
        from_email = os.environ.get('FROM_EMAIL', '(unset)')
        return OK, f'domains=[{names}]; FROM_EMAIL={from_email}'
    if r.status_code in (401, 403):
        return FAIL, f'auth rejected: HTTP {r.status_code}'
    return FAIL, f'HTTP {r.status_code}: {r.text[:200]}'


def live_send_email() -> tuple[str, str]:
    sink = os.environ.get('STAFF_SINK_EMAIL')
    if not sink:
        return MISS, 'STAFF_SINK_EMAIL not set — refusing to send'
    try:
        import resend
        resend.api_key = os.environ['RESEND_API_KEY']
        r = resend.Emails.send({
            'from': os.environ['FROM_EMAIL'],
            'to': [sink],
            'subject': 'Conversion Engine — integration probe',
            'html': 'This is a connectivity test. Safe to ignore.',
        })
        return OK, f'sent id={r.get("id")} to {sink}'
    except Exception as e:
        return FAIL, f'{type(e).__name__}: {e}'


def live_send_sms() -> tuple[str, str]:
    sink = os.environ.get('STAFF_SINK_SMS')
    if not sink:
        return MISS, 'STAFF_SINK_SMS not set — refusing to send'
    try:
        import africastalking
        africastalking.initialize(
            os.environ['AT_USERNAME'], os.environ['AT_API_KEY'])
        r = africastalking.SMS.send(
            'Conversion Engine — integration probe',
            [sink], os.environ.get('AT_SHORTCODE'))
        return OK, f'sent to {sink}: {r}'
    except Exception as e:
        return FAIL, f'{type(e).__name__}: {e}'


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--live-send', action='store_true',
                    help='Also send a test email + SMS to staff sink.')
    args = ap.parse_args()

    checks = [
        ('Cal.com',           check_calcom),
        ('HubSpot',           check_hubspot),
        ("Africa's Talking",  check_africastalking),
        ('Resend (email)',    check_resend),
    ]
    if args.live_send:
        checks.append(('Resend LIVE send', live_send_email))
        checks.append(("AT LIVE send",     live_send_sms))

    results = []
    for name, fn in checks:
        status, detail = fn()
        results.append((name, status, detail))
        flag = {OK: 'OK', FAIL: 'XX', MISS: '??'}[status]
        print(f'[{flag}] {name:20s}  {status:8s}  {detail}')

    print()
    n_ok = sum(1 for _, s, _ in results if s == OK)
    print(f'{n_ok}/{len(results)} checks passed.')
    sys.exit(0 if n_ok == len(results) else 1)


if __name__ == '__main__':
    main()
