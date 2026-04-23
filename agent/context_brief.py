"""Discovery call context brief generator.

Produces the 10-section Markdown briefing document defined by
`schemas/discovery_call_context_brief.md`. The brief is attached to the
Cal.com invite every time the agent books a discovery call, so the
human delivery lead can walk into the call with context already loaded.

This generator fills every section that resolves from:
- hiring_signal_brief.json    → segment, signals, AI maturity
- competitor_gap_brief.json   → gap findings, peer evidence
- seed/bench_summary.json     → bench-to-brief match
- the booking payload         → prospect identity, Cal.com slot

Sections that require live data we don't have at brief-generation time
(thread summary, objections raised, commercial signals, agent
self-reflection) are rendered with explicit `_TODO_` markers rather than
silently omitted — per the schema, "every section below is required
unless marked optional" and a shallow brief is worse than none.

Caller supplies a `booking` dict for the header fields:

    booking = {
        'prospect_first_name': 'Elena',
        'prospect_title': 'Co-founder',
        'prospect_company': 'Orrin Labs',
        'call_datetime_utc': '2026-04-28T14:00:00Z',
        'call_datetime_prospect_tz': '2026-04-28 07:00 Pacific',
        'delivery_lead_name': 'Arun',
        'duration_minutes': 30,
        'thread_start_date': '2026-04-22',
        'original_subject': 'Context: your $14M Series B',
        'langfuse_trace_url': 'https://cloud.langfuse.com/trace/...',
        'trace_id': 'xxx',
    }

Usage:
    from context_brief import build_context_brief
    md = build_context_brief(hiring_brief, gap_brief, booking)
"""
from __future__ import annotations

import json, pathlib, datetime
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parent.parent
BRIEFS_DIR = ROOT / 'data' / 'briefs'
BENCH_PATH = ROOT / 'seed' / 'bench_summary.json'

_TODO = '_TODO_ (filled from Langfuse thread at booking time)'


def _load_bench() -> dict:
    try:
        return json.loads(BENCH_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _fmt_get(d: dict, *path: str, default: Any = '—') -> Any:
    cur: Any = d
    for k in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def _segment_rationale(hiring: dict) -> str:
    """One-line rationale from the _extras the brief carries, or a
    fallback when the extras block has been stripped."""
    extras = hiring.get('_extras', {}) or {}
    rationale = extras.get('segment_rationale') or []
    if rationale:
        return '; '.join(rationale[:3])
    return 'see brief _extras.segment_rationale for per-signal evidence'


def _abstention_risk(hiring: dict) -> str:
    if hiring.get('primary_segment_match') == 'abstain':
        return (f'Yes — classifier abstained '
                f'(conf={hiring.get("segment_confidence", 0):.2f}). '
                f'Send generic exploratory, not a segment-specific pitch.')
    conf = hiring.get('segment_confidence', 0)
    if conf < 0.7:
        return f'Moderate — segment confidence {conf:.2f}.'
    return 'Low — high-confidence segment match.'


def _funding_line(b: dict) -> str:
    fe = _fmt_get(b, 'buying_window_signals', 'funding_event', default={})
    if not fe or not fe.get('detected'):
        return 'No funding event detected within the 180-day window.'
    parts = []
    if fe.get('stage'):
        parts.append(fe['stage'].replace('_', ' ').title())
    if fe.get('amount_usd'):
        parts.append(f'${fe["amount_usd"]:,}')
    parts.append(f'closed {fe.get("closed_at", "—")}')
    src = f' — {fe["source_url"]}' if fe.get('source_url') else ''
    return ' '.join(parts) + src


def _hiring_velocity_line(b: dict) -> str:
    v = b.get('hiring_velocity', {}) or {}
    today = v.get('open_roles_today', 0)
    prior = v.get('open_roles_60_days_ago', 0)
    label = v.get('velocity_label', 'insufficient_signal')
    return (f'{today} open roles today vs {prior} sixty days ago — '
            f'velocity `{label}` (confidence {v.get("signal_confidence", 0)})')


def _layoff_line(b: dict) -> str:
    le = _fmt_get(b, 'buying_window_signals', 'layoff_event', default={})
    if not le or not le.get('detected'):
        return 'Not detected in the last 120 days.'
    parts = [f'Detected {le.get("date", "—")}']
    if le.get('headcount_reduction'):
        parts.append(f'{le["headcount_reduction"]} headcount')
    if le.get('percentage_cut') is not None:
        parts.append(f'{le["percentage_cut"]*100:.0f}%')
    return ', '.join(parts)


def _leadership_line(b: dict) -> str:
    lc = _fmt_get(b, 'buying_window_signals', 'leadership_change', default={})
    if not lc or not lc.get('detected'):
        return 'No engineering leadership transition detected in last 90 days.'
    role = lc.get('role', 'other').replace('_', ' ')
    return (f'New {role} started {lc.get("started_at", "—")}'
            + (f' — {lc["source_url"]}' if lc.get('source_url') else ''))


def _ai_maturity_line(b: dict) -> str:
    am = b.get('ai_maturity', {}) or {}
    score = am.get('score', 0)
    conf = am.get('confidence', 0)
    label = 'low' if conf < 0.5 else 'medium' if conf < 0.7 else 'high'
    return f'{score} / 3 (confidence {conf:.2f}, {label})'


def _gap_section(gap: dict) -> str:
    if not gap or gap.get('skipped'):
        reason = (gap or {}).get('reason', 'gap brief not produced')
        return f'_Gap brief abstained: {reason}._'
    high_conf = [f for f in gap.get('gap_findings', [])
                 if f.get('confidence') == 'high']
    lower_conf = [f for f in gap.get('gap_findings', [])
                  if f.get('confidence') != 'high']
    out: list[str] = []
    if high_conf:
        out.append('**High-confidence findings (discuss freely):**')
        for f in high_conf[:3]:
            peer_names = ', '.join(pe['competitor_name']
                                   for pe in f['peer_evidence'][:3])
            out.append(f'- {f["practice"]} — peers: {peer_names}')
    if lower_conf:
        out.append('')
        out.append('**Lower-confidence findings (ask rather than assert):**')
        for f in lower_conf[:3]:
            peer_names = ', '.join(pe['competitor_name']
                                   for pe in f['peer_evidence'][:3])
            out.append(
                f'- [{f["confidence"]}] {f["practice"]} — peers: {peer_names}')
    pitch = gap.get('suggested_pitch_shift')
    if pitch:
        out.append('')
        out.append(f'_Suggested pitch shift:_ {pitch}')
    return '\n'.join(out) if out else '_No gap findings surfaced._'


def _bench_section(hiring: dict) -> str:
    bench = _load_bench()
    match = hiring.get('bench_to_brief_match', {}) or {}
    required = match.get('required_stacks') or []
    if not required:
        return ('No explicit stack signal in the hiring brief. Do not commit '
                'specific staffing in the call; keep the conversation on the '
                'business problem.')
    lines = [f'**Stacks the prospect will likely need:** {", ".join(required)}']
    if bench and bench.get('stacks'):
        avail_lines = []
        for s in required:
            stack_info = bench['stacks'].get(s, {})
            n = stack_info.get('available_engineers', 0)
            ttd = stack_info.get('time_to_deploy_days')
            avail_lines.append(
                f'- `{s}`: {n} engineers available'
                + (f', {ttd}d time-to-deploy' if ttd else ''))
        lines.append('**Bench availability (seed/bench_summary.json):**')
        lines.extend(avail_lines)
    gaps = match.get('gaps') or []
    lines.append(f'**Gaps:** {", ".join(gaps) if gaps else "none"}')
    lines.append(
        '**Honest flag:** agent did not promise specific staffing in outreach; '
        'numbers above are bench current-state, not a commitment.'
        if match.get('bench_available')
        else '**Honest flag:** bench has a gap for the inferred need; the '
             'agent has flagged `bench_gap_detected` in honesty_flags.')
    return '\n'.join(lines)


def build_context_brief(hiring: dict, gap: dict | None,
                        booking: dict) -> str:
    """Render the 10-section brief. `gap` may be None (or an abstention
    sentinel with skipped=True) — the gap section collapses gracefully."""
    segment = hiring.get('primary_segment_match', 'abstain')
    segment_conf = hiring.get('segment_confidence', 0)
    flags = hiring.get('honesty_flags', [])
    flags_line = ', '.join(flags) if flags else 'none'
    am = hiring.get('ai_maturity', {}) or {}
    am_positive = [j['status'] for j in am.get('justifications', [])
                   if j.get('confidence') in ('high', 'medium')
                   and 'not wired' not in (j.get('status') or '').lower()
                   and 'no ' not in (j.get('status') or '').lower()[:5]]

    md = f"""# Discovery Call Context Brief

**Prospect:** {booking.get('prospect_first_name', '—')} — \
{booking.get('prospect_title', '—')} at {booking.get('prospect_company', '—')}
**Scheduled:** {booking.get('call_datetime_utc', '—')} \
({booking.get('call_datetime_prospect_tz', '—')} prospect local)
**Delivery lead assigned:** {booking.get('delivery_lead_name', '—')}
**Call length booked:** {booking.get('duration_minutes', 30)} minutes
**Thread origin:** {booking.get('thread_start_date', '—')} — \
Email subject: "{booking.get('original_subject', '—')}"
**Full thread:** [Link to Langfuse trace]\
({booking.get('langfuse_trace_url', '#')})

_Brief generated {datetime.datetime.now(datetime.UTC).isoformat()} \
from hiring_signal_brief + competitor_gap_brief + bench_summary._

---

## 1. Segment and confidence

- **Primary segment match:** `{segment}`
- **Confidence:** {segment_conf:.2f}
- **Why this segment:** {_segment_rationale(hiring)}
- **Abstention risk:** {_abstention_risk(hiring)}
- **Honesty flags on the brief:** {flags_line}

## 2. Key signals (from hiring_signal_brief.json)

- **Funding event:** {_funding_line(hiring)}
- **Hiring velocity:** {_hiring_velocity_line(hiring)}
- **Layoff event:** {_layoff_line(hiring)}
- **Leadership change:** {_leadership_line(hiring)}
- **AI maturity score:** {_ai_maturity_line(hiring)}
- **AI-maturity positive evidence:**
{chr(10).join(f'  - {s}' for s in am_positive) if am_positive
  else '  - (none above medium confidence — ask rather than assert)'}

## 3. Competitor gap findings (from competitor_gap_brief.json)

{_gap_section(gap or {})}

## 4. Bench-to-brief match

{_bench_section(hiring)}

## 5. Conversation history summary

{_TODO}

## 6. Objections already raised (and the agent's responses)

{_TODO}

## 7. Commercial signals

- **Price bands already quoted:** {_TODO}
- **Has the prospect asked for a specific total contract value?** {_TODO}
- **Is the prospect comparing vendors?** {_TODO}
- **Urgency signals:** {_TODO}

## 8. Suggested call structure

- **Minutes 0–2:** Open on the segment-specific neutral fact from §2. \
For `{segment}`, lead with the {"funding event" if segment == "segment_1_series_a_b" else "restructure date" if segment == "segment_2_mid_market_restructure" else "transition" if segment == "segment_3_leadership_transition" else "specific capability signal" if segment == "segment_4_specialized_capability" else "exploratory framing"}.
- **Minutes 2–10:** Qualifying question — what is the bottleneck that \
brought them to accept the call?
- **Minutes 10–20:** Capability discussion — only reference stacks where \
the bench has current availability (§4).
- **Minutes 20–25:** Commercial framing — do NOT quote specific pricing \
beyond the public bands in `seed/pricing_sheet.md`.
- **Minutes 25–30:** Specific next step — proposal scope, timeline, \
who-writes-what.

## 9. What NOT to do on this call

- Do not assert any signal flagged `weak_*` in §1.
- Do not commit bench capacity beyond the numbers in §4.
- Do not cite peer-company practices not in §3.
{('- Note: agent abstained on segment classification (' + f'conf {segment_conf:.2f}' + '); avoid segment-specific pitch language.') if segment == 'abstain' else ''}

## 10. Agent confidence and unknowns

- **Things the agent is confident about:** Crunchbase firmographics, \
layoff detection, funding recency, industry-tag signal.
- **Things the agent is uncertain about:** {flags_line}
- **Things the agent could not find:** live job-post velocity (scraper \
deferred), press/investor-letter signal, public GitHub org activity.
- **Overall agent confidence in this brief:** {segment_conf:.2f}

---

*This brief was generated by the TRP1 Week 10 Conversion Engine. \
Trace ID: `{booking.get('trace_id', 'unknown')}`. \
Generated at {datetime.datetime.now(datetime.UTC).isoformat()}.*
"""
    return md


def write_context_brief(prospect_name: str,
                        hiring: dict, gap: dict | None,
                        booking: dict) -> pathlib.Path:
    """Render and save. Returns the path for the caller to attach to a
    Cal.com invite or HubSpot note."""
    from enrichment import _slug
    md = build_context_brief(hiring, gap, booking)
    out = BRIEFS_DIR / f'context_brief_{_slug(prospect_name)}.md'
    out.write_text(md, encoding='utf-8')
    return out
