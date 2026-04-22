import os, json
from dotenv import load_dotenv
from enrichment import build_hiring_signal_brief
from email_handler import send_outreach
from hubspot_handler import upsert_contact, log_note
from langfuse import Langfuse

load_dotenv(override=True)
lf = Langfuse(
    secret_key=os.environ['LANGFUSE_SECRET_KEY'],
    public_key=os.environ['LANGFUSE_PUBLIC_KEY'],
    host=os.environ['LANGFUSE_HOST']
)

def resolve_destination(requested_email: str) -> tuple[str, bool]:
    """Kill-switch gate: return (actual_destination, is_live).

    When LIVE_OUTBOUND is unset or "0", all outbound is routed to the
    staff-controlled sink — per the TRP1 data-handling policy. Only set
    LIVE_OUTBOUND=1 after staff and Tenacious exec written approval.
    """
    live = os.environ.get('LIVE_OUTBOUND', '').strip() == '1'
    if live:
        return requested_email, True
    return os.environ.get('STAFF_SINK_EMAIL', 'sink@example.com'), False

SYSTEM_PROMPT = '''You are an outreach agent for Tenacious Consulting.
You write signal-grounded outreach emails based on hiring briefs.
Rules:
- Only assert what the brief explicitly supports
- If a signal has confidence=low, ASK rather than ASSERT
- Never commit to capacity not in the bench summary
- Keep emails under 150 words
- Tone: direct, peer-to-peer, no jargon
'''

def compose_email(brief: dict) -> dict:
    import requests
    model = os.environ.get('DEV_MODEL', 'deepseek/deepseek-chat')
    payload = {
        'model': model,
        'max_tokens': 500,
        'messages': [
            {'role': 'system', 'content': SYSTEM_PROMPT},
            {'role': 'user',
             'content': f'Write an outreach email for this brief:\n{json.dumps(brief, indent=2)}'},
        ],
    }
    headers = {
        'Authorization': f'Bearer {os.environ["OPENROUTER_API_KEY"]}',
        'Content-Type': 'application/json',
        'HTTP-Referer': 'https://github.com/nahom/conversion-engine',
        'X-Title': 'Conversion Engine - TRP1 Week 10',
    }
    r = requests.post('https://openrouter.ai/api/v1/chat/completions',
                      headers=headers, json=payload, timeout=60)
    if not r.ok:
        raise RuntimeError(
            f'OpenRouter {r.status_code}: {r.text[:500]}')
    content = r.json()['choices'][0]['message']['content']
    lines = [l for l in content.strip().split('\n') if l.strip()]
    subject = lines[0].replace('Subject:', '').strip() if lines else '(no subject)'
    body_html = '<br>'.join(lines[1:]) if len(lines) > 1 else content
    return {'subject': subject, 'html': body_html}

def run_prospect(company: str, email: str, careers_url: str = None):
    with lf.start_as_current_observation(
        name=f'prospect_{company}', as_type='span',
        input={'company': company, 'email': email, 'careers_url': careers_url},
    ) as root_span:
        with lf.start_as_current_observation(
            name='enrichment', as_type='span',
        ) as span:
            brief = build_hiring_signal_brief(company, careers_url)
            span.update(output=brief)

        with lf.start_as_current_observation(
            name='compose', as_type='generation',
            model=os.environ.get('DEV_MODEL', 'deepseek/deepseek-chat'),
            input=brief,
        ) as span:
            email_content = compose_email(brief)
            span.update(output=email_content)

        with lf.start_as_current_observation(
            name='hubspot_write', as_type='span',
        ) as span:
            contact_id = upsert_contact(email, {
                'company': company,
                'hs_lead_status': 'NEW',
                'crunchbase_id': brief.get('crunchbase_id', ''),
                'ai_maturity_score': str(brief.get('ai_maturity', {}).get('score', 0)),
                'last_enriched_at': brief['enriched_at'],
            })
            log_note(contact_id, f'Hiring signal brief:\n{json.dumps(brief, indent=2)}')
            span.update(output={'contact_id': contact_id})

        destination, is_live = resolve_destination(email)
        with lf.start_as_current_observation(
            name='send_email', as_type='span',
            input={'destination': destination, 'live': is_live},
        ) as span:
            email_id = send_outreach(
                destination, email_content['subject'], email_content['html'])
            span.update(output={'email_id': email_id})

        root_span.update(output={
            'email_id': email_id, 'contact_id': contact_id,
            'live_outbound': is_live, 'destination': destination,
        })

    lf.flush()
    mode = 'LIVE' if is_live else 'sink'
    print(f'[{mode}] Sent to {destination} — HubSpot contact: {contact_id}')
    return {'brief': brief, 'email_id': email_id, 'contact_id': contact_id}