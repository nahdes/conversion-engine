import os, json, pathlib
from dotenv import load_dotenv
from enrichment import build_hiring_signal_brief
from email_handler import send_outreach
from hubspot_handler import upsert_contact, log_note
from channel_router import ChannelRouter, ChannelTransitionError
from langfuse import Langfuse

load_dotenv(override=True)
lf = Langfuse(
    secret_key=os.environ['LANGFUSE_SECRET_KEY'],
    public_key=os.environ['LANGFUSE_PUBLIC_KEY'],
    host=os.environ['LANGFUSE_HOST']
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
STYLE_GUIDE_PATH = ROOT / 'seed' / 'style_guide.md'
COLD_SEQUENCE_PATH = ROOT / 'seed' / 'email_sequences' / 'cold.md'

def resolve_destination(requested_email: str) -> tuple[str, bool]:
    """Kill-switch gate: return (actual_destination, is_live).

    TRP1 Rule 5: default-unset. When `TENACIOUS_OUTBOUND_ENABLED` is unset
    or "0", every outbound is routed to the staff-controlled sink. Only set
    the flag to "1" after program staff *and* Tenacious exec written
    approval. Bypassing this gate in code, even for a single test message,
    is a policy violation regardless of outcome.
    """
    live = os.environ.get('TENACIOUS_OUTBOUND_ENABLED', '').strip() == '1'
    if live:
        return requested_email, True
    return os.environ.get('STAFF_SINK_EMAIL', 'sink@example.com'), False

def _load_style_guide() -> str:
    try:
        return STYLE_GUIDE_PATH.read_text(encoding='utf-8')
    except Exception:
        return ''


def _load_cold_sequence() -> str:
    try:
        return COLD_SEQUENCE_PATH.read_text(encoding='utf-8')
    except Exception:
        return ''


# The Tenacious style guide (seed/style_guide.md) is loaded verbatim as
# ground truth for tone. Changes to the guide flow through automatically.
# The cold-sequence template is included so the model sees the canonical
# Email-1 structure and the Segment-1/3 examples without us re-typing them.
SYSTEM_PROMPT = f'''You are the outreach composer for Tenacious Intelligence
Corporation — a B2B talent outsourcing and consulting firm. You write
signal-grounded cold emails that a founder, CTO, or VP Engineering would
read with interest rather than discomfort.

## Absolute rules

1. Every factual claim must map to a field in the hiring_signal_brief or
   the competitor_gap_brief that is passed to you. Do not invent names,
   numbers, dates, or competitor practices.
2. If a brief field has confidence=low or honesty_flags contains a
   matching weak-signal flag, ASK rather than ASSERT. Prefer "we don't
   see public signal of X — is that accurate?" over "you are not doing X."
3. Never commit to bench capacity that bench_summary.json does not show.
   If the brief's bench_to_brief_match has bench_available=false, drop
   the capacity mention entirely and pitch a scoping conversation
   instead. Committing to capacity the bench does not show is a policy
   violation (bench over-commitment probe).
4. Never reproduce Tenacious-branded content verbatim across prospects.
   Each email must be a fresh composition grounded in this specific
   brief. Re-using the sample emails in the cold-sequence file below
   verbatim is a tone violation.
5. Mark all output as draft. Do not claim approval the human reviewer
   has not given.

## Segment-aware pitch language

The brief carries primary_segment_match and ai_maturity.score. Match:

- segment_1_series_a_b, ai>=2: "scale your AI team faster than in-house
  hiring can support"
- segment_1_series_a_b, ai<=1: "stand up your first AI function with a
  dedicated squad"
- segment_2_mid_market_restructure, ai>=2: "preserve your AI delivery
  capacity while reshaping cost structure"
- segment_2_mid_market_restructure, ai<=1: "maintain platform delivery
  velocity through the restructure"
- segment_3_leadership_transition: AI score does NOT shift the pitch.
  Open on the transition as a neutral fact; let the leader direct the
  technical language.
- segment_4_specialized_capability (ai>=2 only): lead with the specific
  peer-gap finding from competitor_gap_brief.gap_findings. Frame as a
  research finding, not a deficiency.
- abstain: send a generic exploratory email. No segment-specific pitch,
  no capacity commitment.

## Output format

Return exactly two lines:

```
Subject: <subject line, under 60 characters>
<one blank line>
<HTML email body, under 120 words>
```

Use <br> for line breaks in the HTML body. Close with the signature:

[First name]
Research Partner
Tenacious Intelligence Corporation
gettenacious.com

---

# Tenacious Style Guide (seed/style_guide.md)

{_load_style_guide()}

---

# Cold-sequence structure (seed/email_sequences/cold.md)

{_load_cold_sequence()}
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
            # Event point 1 of 3 for HubSpot in this flow:
            #   1. upsert + enrichment fields here
            #   2. log_note with the full brief JSON (audit evidence)
            #   3. router.on_email_send after Resend accepts (below)
            contact_id = upsert_contact(email, {
                'company': company,
                'hs_lead_status': 'NEW',
                'tenacious_channel_state': 'cold',
                'crunchbase_id': brief.get('_extras', {}).get('crunchbase_id', ''),
                'ai_maturity_score': str(brief['ai_maturity']['score']),
                'primary_segment_match': brief.get('primary_segment_match', ''),
                'last_enriched_at': brief['generated_at'],
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
            # Every channel advance goes through the router so warm-lead
            # gating, CRM state, and audit notes stay in one place.
            router = ChannelRouter(contact_id, contact_props={
                'tenacious_channel_state': 'cold'})
            router.on_email_send(email_id=email_id,
                                 subject=email_content['subject'],
                                 destination=destination,
                                 is_live=is_live)
            span.update(output={'email_id': email_id,
                                'router_state': router.state.value})

        root_span.update(output={
            'email_id': email_id, 'contact_id': contact_id,
            'live_outbound': is_live, 'destination': destination,
            'router_state': router.state.value,
        })

    lf.flush()
    mode = 'LIVE' if is_live else 'sink'
    print(f'[{mode}] Sent to {destination} — HubSpot contact: {contact_id}')
    return {'brief': brief, 'email_id': email_id, 'contact_id': contact_id}