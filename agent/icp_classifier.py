"""ICP segment classifier — seed/icp_definition.md §"Classification rules".

Pure-function implementation of the five-rule precedence:

    1. layoff in 120 days AND fresh funding          → segment_2
    2. new CTO/VP Eng in 90 days                     → segment_3
    3. specialized capability signal AND ai >= 2     → segment_4
    4. fresh funding in 180 days                     → segment_1
    5. otherwise                                     → abstain

Input is a `signals` dict the caller assembles from the enrichment output
(this module intentionally doesn't import enrichment — keeps the
classification logic testable without the full pipeline). Output is the
`(primary_segment_match, segment_confidence, rationale, disqualifiers)`
tuple the hiring_signal_brief schema needs.

Confidence = hits / (hits + inferred_weak_signals). Clamped to [0, 1].
When confidence < 0.6 the caller should flip the segment to `abstain`
per the seed spec; this module does that flip so callers don't have to.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Segment = Literal[
    'segment_1_series_a_b',
    'segment_2_mid_market_restructure',
    'segment_3_leadership_transition',
    'segment_4_specialized_capability',
    'abstain',
]

ABSTAIN_THRESHOLD = 0.6


@dataclass
class ICPSignals:
    """The slice of enrichment output relevant to segment classification.

    Every field is optional; `None` / `False` / `0` means "no evidence",
    not "signal is absent". Absence is reported as low confidence rather
    than as a strong negative."""
    # Funding
    funding_detected: bool = False
    funding_days_ago: int | None = None
    funding_stage: str | None = None
    funding_amount_usd: int | None = None
    # Layoff
    layoff_detected: bool = False
    layoff_days_ago: int | None = None
    layoff_percentage_cut: float | None = None
    # Leadership change
    leadership_change_detected: bool = False
    leadership_change_role: str | None = None   # e.g. 'cto', 'vp_engineering'
    leadership_change_days_ago: int | None = None
    leadership_change_is_interim: bool = False
    # Hiring velocity / specialized-capability signal
    eng_roles_open: int = 0
    specialized_role_repeated_60d: bool = False
    ai_maturity_score: int = 0
    # Firmographics
    num_employees_band: str | None = None        # e.g. '15-80', '80-200'
    country_code: str | None = None
    # Disqualifiers caller can pass through when known
    anti_offshore_public_stance: bool = False
    competitor_case_studied: bool = False


def _headcount_in(band: str | None, lo: int, hi: int) -> bool:
    """Crunchbase's `num_employees` column is a dash-separated range string
    like '15-80'. Return True when *any* of the band's endpoints fall
    within [lo, hi]. Matches ICP filter semantics loosely — missing band
    is not a disqualifier, only weak confidence."""
    if not band:
        return False
    try:
        parts = band.replace('+', '-').split('-')
        endpoints = [int(p.strip()) for p in parts if p.strip().isdigit()]
    except Exception:
        return False
    return any(lo <= e <= hi for e in endpoints)


def _score_segment_1(s: ICPSignals) -> tuple[bool, list[str], list[str]]:
    """Series A/B, 5–30M, 180 days, 15–80 headcount, ≥5 eng roles."""
    hits, soft, disq = [], [], []
    if s.funding_detected and s.funding_days_ago is not None \
            and s.funding_days_ago <= 180:
        hits.append(f'funding_event <=180d ({s.funding_days_ago}d ago)')
    if s.funding_stage in ('series_a', 'series_b'):
        hits.append(f'stage={s.funding_stage}')
    else:
        soft.append('stage_unknown_or_wrong')
    if s.funding_amount_usd and 5_000_000 <= s.funding_amount_usd <= 30_000_000:
        hits.append(f'amount=${s.funding_amount_usd:,}')
    elif s.funding_amount_usd:
        soft.append(f'amount=${s.funding_amount_usd:,} outside $5-30M')
    if _headcount_in(s.num_employees_band, 15, 80):
        hits.append(f'headcount={s.num_employees_band}')
    else:
        soft.append(f'headcount={s.num_employees_band}')
    if s.eng_roles_open >= 5:
        hits.append(f'{s.eng_roles_open} open eng roles')
    else:
        soft.append(f'{s.eng_roles_open} open eng roles (<5)')
    # Disqualifiers
    if s.layoff_detected and s.layoff_days_ago is not None \
            and s.layoff_days_ago <= 90 \
            and s.layoff_percentage_cut is not None \
            and s.layoff_percentage_cut > 0.15:
        disq.append('layoff >15% in last 90d (ICP maps to segment_2)')
    if s.anti_offshore_public_stance:
        disq.append('anti-offshore public stance')
    if s.competitor_case_studied:
        disq.append('listed as client of a direct competitor')
    return (
        s.funding_detected and s.funding_days_ago is not None
        and s.funding_days_ago <= 180 and not disq
    ), hits, (soft + disq)


def _score_segment_2(s: ICPSignals) -> tuple[bool, list[str], list[str]]:
    """Mid-market, layoff in 120 days OR post-restructure, 200–2000,
    still hiring ≥3."""
    hits, soft, disq = [], [], []
    if s.layoff_detected and s.layoff_days_ago is not None \
            and s.layoff_days_ago <= 120:
        hits.append(f'layoff <=120d ({s.layoff_days_ago}d ago)')
    if _headcount_in(s.num_employees_band, 200, 2000):
        hits.append(f'headcount={s.num_employees_band}')
    else:
        soft.append(f'headcount={s.num_employees_band}')
    if s.eng_roles_open >= 3:
        hits.append(f'{s.eng_roles_open} open eng roles (still hiring)')
    elif s.eng_roles_open == 0:
        disq.append('0 eng roles — signals frozen hire')
    if s.layoff_percentage_cut is not None and s.layoff_percentage_cut > 0.40:
        disq.append(f'layoff {s.layoff_percentage_cut:.0%} >40% (survival mode)')
    return (
        s.layoff_detected and s.layoff_days_ago is not None
        and s.layoff_days_ago <= 120 and not disq
    ), hits, (soft + disq)


def _score_segment_3(s: ICPSignals) -> tuple[bool, list[str], list[str]]:
    """New CTO/VP Eng in last 90 days, 50–500."""
    hits, soft, disq = [], [], []
    if s.leadership_change_detected \
            and s.leadership_change_role in ('cto', 'vp_engineering',
                                             'chief_data_officer',
                                             'head_of_ai') \
            and s.leadership_change_days_ago is not None \
            and s.leadership_change_days_ago <= 90:
        hits.append(
            f'new {s.leadership_change_role} '
            f'{s.leadership_change_days_ago}d ago')
    if _headcount_in(s.num_employees_band, 50, 500):
        hits.append(f'headcount={s.num_employees_band}')
    else:
        soft.append(f'headcount={s.num_employees_band}')
    if s.leadership_change_is_interim:
        disq.append('interim appointment')
    return (
        s.leadership_change_detected
        and s.leadership_change_days_ago is not None
        and s.leadership_change_days_ago <= 90 and not disq
    ), hits, (soft + disq)


def _score_segment_4(s: ICPSignals) -> tuple[bool, list[str], list[str]]:
    """Specialized capability signal AND AI readiness >= 2."""
    hits, soft, disq = [], [], []
    if s.specialized_role_repeated_60d:
        hits.append('specialized role repeated 60+d unfilled')
    else:
        soft.append('no repeated specialized role signal')
    if s.ai_maturity_score >= 2:
        hits.append(f'ai_maturity_score={s.ai_maturity_score}')
    else:
        disq.append(
            f'ai_maturity_score={s.ai_maturity_score} (<2, policy skip)')
    return (
        s.specialized_role_repeated_60d and s.ai_maturity_score >= 2
        and not disq
    ), hits, (soft + disq)


def _confidence(hits: list[str], soft: list[str]) -> float:
    """hits / (hits + soft). Empty hits → 0. Disqualifiers never reach
    here because they short-circuit the segment match upstream."""
    total = len(hits) + len(soft)
    if total == 0:
        return 0.0
    return round(len(hits) / total, 2)


def classify(s: ICPSignals) -> dict:
    """Apply the 5-rule precedence from seed/icp_definition.md.
    Returns a dict with primary_segment_match, segment_confidence, the
    per-rule rationale, and the disqualifiers that were considered."""
    candidates: list[tuple[Segment, tuple[bool, list[str], list[str]]]] = []

    # Precedence rule 1: layoff in 120d AND fresh funding → segment 2
    seg2 = _score_segment_2(s)
    if seg2[0] and s.funding_detected \
            and s.funding_days_ago is not None \
            and s.funding_days_ago <= 180:
        candidates.append(('segment_2_mid_market_restructure', seg2))

    # Rule 2: new CTO/VP Eng in 90d → segment 3
    if not candidates:
        seg3 = _score_segment_3(s)
        if seg3[0]:
            candidates.append(('segment_3_leadership_transition', seg3))

    # Rule 3: specialized-capability AND ai>=2 → segment 4
    if not candidates:
        seg4 = _score_segment_4(s)
        if seg4[0]:
            candidates.append(('segment_4_specialized_capability', seg4))

    # Rule 4: fresh funding 180d → segment 1
    if not candidates:
        seg1 = _score_segment_1(s)
        if seg1[0]:
            candidates.append(('segment_1_series_a_b', seg1))

    # Rule 5: abstain
    if not candidates:
        return {
            'primary_segment_match': 'abstain',
            'segment_confidence': 0.0,
            'rationale': ['no qualifying segment rule fired'],
            'disqualifiers': [],
        }

    segment, (_, hits, soft) = candidates[0]
    conf = _confidence(hits, soft)
    if conf < ABSTAIN_THRESHOLD:
        return {
            'primary_segment_match': 'abstain',
            'segment_confidence': conf,
            'rationale': [f'would match {segment} but confidence '
                          f'{conf:.2f} < {ABSTAIN_THRESHOLD}'] + hits,
            'disqualifiers': soft,
        }
    return {
        'primary_segment_match': segment,
        'segment_confidence': conf,
        'rationale': hits,
        'disqualifiers': soft,
    }
