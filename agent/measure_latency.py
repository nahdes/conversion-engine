"""Cheap synthetic latency sweep for Act II.

Runs the enrichment + gap-brief pipeline across 20 Crunchbase prospects,
records per-step and end-to-end latency, and writes
`eval/latency_log.json` with p50/p95 per stage.

By default the LLM compose and outbound send are mocked so this run costs
nothing and does not depend on OpenRouter / Resend / HubSpot availability.
Pass --real-llm to exercise OpenRouter (uses `compose_email` from
main_agent). Outbound send (Resend) and HubSpot upsert stay mocked — the
kill-switch would sink them anyway during the challenge week.

Usage:
    python agent/measure_latency.py              # all-mock, fast
    python agent/measure_latency.py --real-llm   # real compose via OpenRouter
    python agent/measure_latency.py --n 10       # fewer prospects
"""
from __future__ import annotations

import argparse, json, os, pathlib, random, statistics, sys, time
from dotenv import load_dotenv

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from enrichment import (
    build_hiring_signal_brief,
    build_competitor_gap_brief,
    load_crunchbase,
    _industries,
    _builtwith_names,
    AI_INDUSTRY_TOKENS,
    AI_DESCRIPTION_TOKENS,
    AI_TECH_TOKENS,
)

load_dotenv()


def _ai_signal_rank(row: dict) -> int:
    """Rough AI-signal rank. Higher = more likely to surface a gap or a score."""
    inds = [i.lower() for i in _industries(row)]
    techs = _builtwith_names(row)
    desc = ((row.get('about') or '') +
            ' ' + (row.get('full_description') or '')).lower()
    ai_ind = sum(1 for i in inds
                 if any(tok in i for tok in AI_INDUSTRY_TOKENS))
    ai_tech = sum(1 for t in techs
                  if any(tok in t for tok in AI_TECH_TOKENS))
    ai_desc = sum(1 for tok in AI_DESCRIPTION_TOKENS if tok in desc)
    return ai_ind * 3 + ai_tech * 2 + ai_desc


def _pick_prospects(n: int, seed: int = 42) -> list[str]:
    """Stratified pick: half from companies with AI signal (so gap briefs
    surface comparisons), half random-ish, all constrained to rows with
    `similar_companies` so competitor discovery has real material to work
    with."""
    rows = load_crunchbase()
    with_sims = [r for r in rows
                 if r.get('similar_companies') not in (None, '', '[]')
                 and r.get('name')]
    ranked = sorted(with_sims, key=_ai_signal_rank, reverse=True)
    top_half = max(1, n // 2)
    signal_picks = ranked[:top_half]

    rng = random.Random(seed)
    remaining = [r for r in ranked[top_half:] if r not in signal_picks]
    rng.shuffle(remaining)
    rest = remaining[:n - top_half]
    return [r['name'] for r in (signal_picks + rest)]


def _mock_compose(brief: dict) -> dict:
    """Stubbed compose: no external call. Latency is the time to build the
    string, which is essentially zero — callers log this separately so
    downstream p50/p95 is not inflated by a fake sleep."""
    company = brief.get('company', '(unknown)')
    score = brief.get('ai_maturity', {}).get('score', 0)
    return {
        'subject': f'Following up — {company}',
        'html': (f'Hi there,<br><br>We saw public signal on {company}. '
                 f'AI-maturity score {score}/3. Want to compare notes?'),
        'mocked': True,
    }


def _real_compose(brief: dict) -> dict:
    # Imported lazily so the mock path doesn't require requests/env keys.
    from main_agent import compose_email
    out = compose_email(brief)
    out['mocked'] = False
    return out


def _percentiles(xs: list[float]) -> dict:
    if not xs:
        return {'p50': None, 'p95': None, 'mean': None, 'max': None}
    xs_sorted = sorted(xs)
    p95_idx = min(len(xs_sorted) - 1, int(0.95 * len(xs_sorted)))
    return {
        'p50': round(statistics.median(xs_sorted), 3),
        'p95': round(xs_sorted[p95_idx], 3),
        'mean': round(statistics.mean(xs_sorted), 3),
        'max': round(max(xs_sorted), 3),
    }


def run_sweep(n: int, real_llm: bool) -> dict:
    compose_fn = _real_compose if real_llm else _mock_compose
    prospects = _pick_prospects(n)

    rows: list[dict] = []
    for i, company in enumerate(prospects, 1):
        started = time.time()
        t0 = time.time()
        brief = build_hiring_signal_brief(company)
        enrich_s = time.time() - t0

        t0 = time.time()
        gap = build_competitor_gap_brief(company)
        gap_s = time.time() - t0

        t0 = time.time()
        try:
            email = compose_fn(brief)
            compose_s = time.time() - t0
            compose_error = None
        except Exception as e:
            compose_s = time.time() - t0
            email = None
            compose_error = f'{type(e).__name__}: {e}'

        # HubSpot + Resend stay mocked: tiny bookkeeping hop.
        t0 = time.time()
        hubspot_id = f'mock_contact_{i}'
        email_id = f'mock_email_{i}'
        outbound_s = time.time() - t0

        total_s = time.time() - started
        extras = brief.get('_extras', {})
        rows.append({
            'i': i,
            'company': company,
            'prospect_domain': brief.get('prospect_domain'),
            'crunchbase_match': extras.get('crunchbase_matched'),
            'primary_industry': extras.get('primary_industry'),
            'primary_segment_match': brief.get('primary_segment_match'),
            'segment_confidence': brief.get('segment_confidence'),
            'ai_maturity_score': brief['ai_maturity']['score'],
            'honesty_flags': brief.get('honesty_flags', []),
            'gap_count': len(gap.get('gaps', [])),
            'competitors_evaluated': gap.get('competitors_evaluated', 0),
            'top_quartile_score_avg': gap.get('top_quartile_score_avg', 0),
            'enrich_s': round(enrich_s, 3),
            'gap_s': round(gap_s, 3),
            'compose_s': round(compose_s, 3),
            'outbound_s': round(outbound_s, 4),
            'total_s': round(total_s, 3),
            'compose_error': compose_error,
            'mocked_compose': not real_llm,
            'mocked_outbound': True,
            'mocked_hubspot': True,
        })
        err = f' compose_error={compose_error}' if compose_error else ''
        print(f'[{i:2d}/{n}] {company[:30]:30s} '
              f'enrich={enrich_s:5.2f}s gap={gap_s:5.2f}s '
              f'compose={compose_s:5.2f}s total={total_s:5.2f}s'
              f' ai={brief["ai_maturity"]["score"]}'
              f' gaps={len(gap.get("gaps", []))}{err}')

    summary = {
        'n_prospects': len(rows),
        'real_llm': real_llm,
        'mocked_outbound': True,
        'mocked_hubspot': True,
        'per_stage': {
            'enrich': _percentiles([r['enrich_s'] for r in rows]),
            'gap':    _percentiles([r['gap_s'] for r in rows]),
            'compose': _percentiles([r['compose_s'] for r in rows]),
            'outbound': _percentiles([r['outbound_s'] for r in rows]),
            'total':  _percentiles([r['total_s'] for r in rows]),
        },
        'crunchbase_match_rate': round(
            sum(1 for r in rows if r['crunchbase_match']) / max(len(rows), 1),
            3),
        'gaps_surfaced_rate': round(
            sum(1 for r in rows if r['gap_count'] > 0) / max(len(rows), 1),
            3),
        'compose_errors': sum(1 for r in rows if r['compose_error']),
    }
    return {'summary': summary, 'prospects': rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=20)
    ap.add_argument('--real-llm', action='store_true',
                    help='Hit OpenRouter for compose; otherwise stub.')
    ap.add_argument('--out', default=str(ROOT / 'eval' / 'latency_log.json'))
    args = ap.parse_args()

    result = run_sweep(args.n, args.real_llm)
    pathlib.Path(args.out).write_text(
        json.dumps(result, indent=2), encoding='utf-8')

    s = result['summary']
    print()
    print('=== p50 / p95 latency (seconds) ===')
    for stage, pct in s['per_stage'].items():
        print(f'  {stage:10s}  p50={pct["p50"]}  p95={pct["p95"]}  '
              f'mean={pct["mean"]}  max={pct["max"]}')
    print()
    print(f'Crunchbase match rate:  {s["crunchbase_match_rate"]*100:.1f}%')
    print(f'Prospects with gaps:    {s["gaps_surfaced_rate"]*100:.1f}%')
    print(f'Compose errors:         {s["compose_errors"]}')
    print(f'\nWrote {args.out}')


if __name__ == '__main__':
    main()
