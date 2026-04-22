import os, json, csv, datetime, pathlib
from dotenv import load_dotenv

load_dotenv()

ROOT = pathlib.Path(__file__).resolve().parent.parent
CRUNCHBASE_CSV_PATH = ROOT / 'data' / 'crunchbase-companies-information.csv'
LAYOFFS_CSV_PATH    = ROOT / 'data' / 'layoffs.csv'
BRIEFS_DIR          = ROOT / 'data' / 'briefs'
BRIEFS_DIR.mkdir(parents=True, exist_ok=True)

def _now_iso() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()

def _slug(s: str) -> str:
    return ''.join(c if c.isalnum() else '_' for c in s.lower())[:40]

def _first(row: dict, *keys, default=None):
    """Return the first non-empty value among the given CSV column candidates."""
    for k in keys:
        v = row.get(k)
        if v not in (None, '', 'NA'):
            return v
    return default

def load_crunchbase() -> list[dict]:
    if not CRUNCHBASE_CSV_PATH.exists():
        return []
    with open(CRUNCHBASE_CSV_PATH, encoding='utf-8', errors='replace') as f:
        return list(csv.DictReader(f))

def match_company(name: str) -> dict | None:
    """Case-insensitive substring match on the company-name column."""
    target = name.lower().strip()
    for r in load_crunchbase():
        cand = (_first(r, 'name', 'company_name', 'Organization Name',
                       default='') or '').lower()
        if target and target in cand:
            return r
    return None

def check_layoffs(company_name: str) -> dict:
    if not LAYOFFS_CSV_PATH.exists():
        return {'layoff_events': [], 'has_recent_layoff': False,
                'confidence': 'low', 'note': 'layoffs.csv not loaded'}
    cutoff = datetime.datetime.now() - datetime.timedelta(days=120)
    events = []
    with open(LAYOFFS_CSV_PATH, encoding='utf-8', errors='replace') as f:
        for row in csv.DictReader(f):
            if company_name.lower() in (row.get('Company') or
                                        row.get('company') or '').lower():
                raw_date = row.get('Date') or row.get('date') or ''
                try:
                    d = datetime.datetime.strptime(raw_date[:10], '%Y-%m-%d')
                    if d >= cutoff:
                        events.append(row)
                except Exception:
                    pass
    return {'layoff_events': events,
            'has_recent_layoff': bool(events),
            'confidence': 'high' if events else 'medium'}

def check_funding_recency(cb_row: dict) -> dict:
    """TRP1 signal: funding event in last 180 days."""
    raw = _first(cb_row, 'last_funding_at', 'last_funding_on',
                 'last_funding_date')
    if not raw:
        return {'recent_funding': False, 'confidence': 'low'}
    try:
        d = datetime.datetime.strptime(str(raw)[:10], '%Y-%m-%d')
    except Exception:
        return {'recent_funding': False, 'confidence': 'low'}
    days = (datetime.datetime.now() - d).days
    return {'recent_funding': 0 <= days <= 180,
            'days_since_funding': days,
            'last_funding_at': str(raw)[:10],
            'confidence': 'high'}

def scrape_job_posts(careers_url: str | None) -> dict:
    """Stubbed — Playwright Chromium download failed in this environment.
    Live crawl wired on Day 3."""
    return {'total_roles': 0, 'eng_roles': 0, 'ai_roles': 0,
            'raw_lines': [], 'confidence': 'low',
            'note': f'stub (Chromium not installed); would scrape {careers_url}'
                    if careers_url else 'no careers_url provided'}

def detect_leadership_change(cb_row: dict) -> dict:
    """TRP1 signal: new CTO/VP Eng in last 90 days.
    Press-release / LinkedIn scrape not yet wired — stub."""
    return {'recent_leadership_change': False, 'confidence': 'low',
            'note': 'detection not yet implemented'}

def score_ai_maturity(job_signals: dict, extras: dict | None = None) -> dict:
    """0-3 integer with per-input justification."""
    extras = extras or {}
    score = 0.0
    evidence = []
    eng = max(job_signals.get('eng_roles', 0), 1)
    ai_frac = job_signals.get('ai_roles', 0) / eng
    if ai_frac >= 0.2 and job_signals.get('ai_roles', 0) > 0:
        score += 1
        evidence.append(
            f'{job_signals["ai_roles"]}/{eng} eng roles are AI-adjacent (high)')
    if extras.get('has_ai_leadership'):
        score += 1
        evidence.append('Named AI/ML leadership (high)')
    if extras.get('modern_ml_stack'):
        score += 0.5
        evidence.append('Modern ML stack detected (low)')
    score_int = min(3, round(score))
    conf = 'high' if len(evidence) >= 2 else 'medium' if evidence else 'low'
    return {'score': score_int, 'confidence': conf, 'evidence': evidence}

def build_hiring_signal_brief(company_name: str,
                              careers_url: str | None = None) -> dict:
    cb = match_company(company_name) or {}
    funding_usd = _first(cb, 'funding_total_usd', 'total_funding_usd',
                         'funding_total')
    try:
        funding_usd = float(funding_usd) if funding_usd else None
    except Exception:
        funding_usd = None

    brief = {
        'company': company_name,
        'crunchbase_match': bool(cb),
        'crunchbase_name': _first(cb, 'name', 'company_name',
                                  default=company_name),
        'market': _first(cb, 'market', 'category_list', 'industry'),
        'country': _first(cb, 'country_code', 'country'),
        'status': _first(cb, 'status'),
        'funding_usd': funding_usd,
        'last_funding_type': _first(cb, 'last_funding_type',
                                    'last_funding_round_type'),
        'funding_recency': check_funding_recency(cb),
        'layoffs': check_layoffs(company_name),
        'leadership_change': detect_leadership_change(cb),
        'job_signals': scrape_job_posts(careers_url),
        'enriched_at': _now_iso(),
    }
    brief['ai_maturity'] = score_ai_maturity(brief['job_signals'])
    out = BRIEFS_DIR / f'hiring_signal_brief_{_slug(company_name)}.json'
    out.write_text(json.dumps(brief, indent=2), encoding='utf-8')
    return brief

def build_competitor_gap_brief(company_name: str, sector: str,
                               competitors: list[str]) -> dict:
    """TRP1 required: 5-10 competitors, AI-maturity each, extract 2-3 gaps."""
    prospect = build_hiring_signal_brief(company_name)
    comps = [build_hiring_signal_brief(c) for c in competitors[:10]]

    comp_scores = sorted(
        (c['ai_maturity']['score'] for c in comps), reverse=True)
    tq_n = max(1, len(comp_scores) // 4)
    top_quartile = comp_scores[:tq_n]

    gaps: list[str] = []
    for c in comps:
        if c['ai_maturity']['score'] > prospect['ai_maturity']['score']:
            for e in c['ai_maturity']['evidence']:
                if e not in gaps:
                    gaps.append(e)

    brief = {
        'prospect': company_name,
        'sector': sector,
        'prospect_ai_maturity': prospect['ai_maturity'],
        'top_quartile_score_avg': (sum(top_quartile) / len(top_quartile))
                                  if top_quartile else 0,
        'competitors_evaluated': len(comps),
        'competitor_scores': [
            {'name': c['crunchbase_name'],
             'score': c['ai_maturity']['score'],
             'confidence': c['ai_maturity']['confidence']}
            for c in comps
        ],
        'gaps': gaps[:3],
        'enriched_at': _now_iso(),
    }
    out = BRIEFS_DIR / f'competitor_gap_brief_{_slug(company_name)}.json'
    out.write_text(json.dumps(brief, indent=2), encoding='utf-8')
    return brief
