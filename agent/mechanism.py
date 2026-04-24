"""Act IV mechanism — signal-confidence-aware composition.

Three variants on top of the V0 compose_email baseline, each targeting
the signal over-claiming failure mode named in
`probes/target_failure_mode.md`:

- **V1 — pre-compose brief transform.** Deterministic. Reads
  `honesty_flags` on the hiring-signal brief, injects a structured
  `phrasing_constraints` block, and rewrites weak-signal fields into
  ask-not-assert template slots. Zero incremental LLM cost.

- **V2 — post-compose tone judge.** Deterministic regex check mirroring
  the `probes/probe_library.md` signatures (`P-SIG-01`, `P-TONE-01`,
  `P-GAP-02`, subject-line rules from `style_guide.md`). On violation,
  regenerate with a corrective instruction. At most one regeneration
  to cap cost.

- **V3 — V1 + V2.** Combined.

The tone judge ships as a deterministic proxy for what the challenge
doc calls a "small-model tone-preservation check". The regex catches
the same over-claim family the probe library defines, at zero marginal
cost; a real small-model judge is wired behind a `--live-judge` flag
for the sealed held-out final run.

Every mechanism call returns a structured trace so the ablation runner
can log which constraints fired, how many regenerations occurred, and
what the judge flagged. See `eval/tenacious_holdout.py` for the
ablation driver.
"""
from __future__ import annotations

import os, re, json, time
from dataclasses import dataclass, field, asdict
from typing import Literal

Variant = Literal['V0', 'V1', 'V2', 'V3']

# --------------------------------------------------------------
# Tone-judge lexicons. Every entry is keyed to a probe id so the
# judge's verdict is traceable back to the probe library.
# --------------------------------------------------------------

_CLICHE_PHRASES = {   # style_guide.md §4 "Never use offshore-vendor clichés"
    'world-class':      'P-TONE-01',
    'top talent':       'P-TONE-01',
    'rockstar':         'P-TONE-01',
    'ninja':            'P-TONE-01',
    'a-player':         'P-TONE-01',
    'a players':        'P-TONE-01',
    'cream of the crop':'P-TONE-01',
}
_OVERCLAIM_PATTERNS = [   # P-SIG-01 signatures
    (re.compile(r'\baggressive(?:ly)?\b', re.I),             'P-SIG-01'),
    (re.compile(r'\brapidly scaling\b', re.I),               'P-SIG-01'),
    (re.compile(r'\bhiring at speed\b', re.I),               'P-SIG-01'),
    (re.compile(r'\bvelocity outstripping\b', re.I),         'P-SIG-01'),
    (re.compile(r'\bscaling fast\b', re.I),                  'P-SIG-01'),
]
_CONDESCENDING_PATTERNS = [   # P-TONE-02 / P-GAP-02
    (re.compile(r'\byou are (?:missing|behind|falling behind)\b', re.I),
     'P-GAP-02'),
    (re.compile(r'\byou need to catch up\b', re.I),          'P-GAP-02'),
    (re.compile(r'\byour team clearly can(?:not|.?t)\b', re.I),
     'P-TONE-02'),
]
_SUBJECT_BAD_OPENERS = {'hey', 'just', 'quick'}   # style_guide.md §1

# --------------------------------------------------------------
# Pre-compose transform (V1)
# --------------------------------------------------------------

# Honesty-flag → constraint map. Each entry lists a forbidden assertion
# pattern and the required ask-not-assert replacement hedge that the
# system prompt must force the model to use. The constraints are
# written into the brief so the LLM sees them alongside the signal.
_FLAG_CONSTRAINTS = {
    'weak_hiring_velocity_signal': {
        'forbid': ['aggressive hiring', 'scaling rapidly',
                   'velocity outstripping', 'hiring at speed'],
        'required_hedge':
            ('Phrase hiring as a question not an assertion. '
             'Prefer: "You have N open <stack> roles — is hiring '
             'velocity matching the runway?" Never claim velocity '
             'without >=5 open roles.'),
    },
    'weak_ai_maturity_signal': {
        'forbid': ['your AI strategy', 'AI-ready', 'your ML team'],
        'required_hedge':
            ('AI-maturity signal is weak. Do not assert the prospect '
             'has or lacks AI capability. Ask a neutral scoping '
             'question or omit AI entirely.'),
    },
    'bench_gap_detected': {
        'forbid': ['we can staff', 'we have', 'ready to deploy',
                   'available capacity'],
        'required_hedge':
            ('Bench has gaps for the required stack. Do not commit '
             'capacity. Propose a phased ramp or route to a human.'),
    },
    'layoff_overrides_funding': {
        'forbid': ['fresh budget', 'new funding', 'closed a round'],
        'required_hedge':
            ('Layoff precedes funding; this is a Segment-2 cost-pressure '
             'pitch, not a Segment-1 scaling pitch. Lead with '
             '"preserve delivery capacity".'),
    },
    'tech_stack_inferred_not_confirmed': {
        'forbid': ['your stack', 'you run', 'you use'],
        'required_hedge':
            ('Tech stack is inferred from BuiltWith signal, not '
             'confirmed. Phrase as "we see public signal of X — is '
             'that still accurate?"'),
    },
}


@dataclass
class MechanismTrace:
    """Structured side-channel recording what each mechanism stage did.
    Emitted alongside the composed email so the ablation runner can
    attribute downstream pass/fail to a specific stage."""
    variant: str
    v1_flags_applied: list[str] = field(default_factory=list)
    v2_judge_fired: bool = False
    v2_violations: list[dict] = field(default_factory=list)
    v2_regen_count: int = 0
    compose_calls: int = 0
    latency_s: float = 0.0
    cost_usd: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def transform_brief_v1(brief: dict) -> tuple[dict, list[str]]:
    """Rewrite the brief in-place with a `phrasing_constraints` block
    keyed on its honesty flags. Returns (new_brief, applied_flags).

    V1 is deterministic — given the same brief the output is identical —
    so the transform is safe to cache and replay."""
    flags = list(brief.get('honesty_flags') or [])
    applied: list[str] = []
    constraints: list[dict] = []
    for flag in flags:
        c = _FLAG_CONSTRAINTS.get(flag)
        if not c:
            continue
        applied.append(flag)
        constraints.append({
            'triggered_by_flag': flag,
            'forbid_phrasings': c['forbid'],
            'required_hedge': c['required_hedge'],
        })
    # Always emit the block (even empty) so the LLM sees the structured
    # contract; "no constraints" is itself a signal.
    new_brief = dict(brief)
    new_brief['phrasing_constraints'] = constraints
    return new_brief, applied


# --------------------------------------------------------------
# Post-compose tone judge (V2)
# --------------------------------------------------------------

def tone_judge(subject: str, body: str) -> list[dict]:
    """Deterministic proxy for the small-model tone-preservation pass.

    Returns a list of violation records `{probe_id, pattern, excerpt}`.
    Empty list means the email passes the gate. The regex catalogue
    mirrors the probe-library signatures so judge verdicts and probe
    verdicts reference the same rule."""
    out: list[dict] = []
    blob = f'{subject}\n{body}'

    # Subject-line opener rule (style_guide.md §1)
    first_word = (subject or '').strip().split(' ', 1)[0].lower()
    if first_word in _SUBJECT_BAD_OPENERS:
        out.append({'probe_id': 'P-TONE-03',
                    'pattern': 'subject_bad_opener',
                    'excerpt': first_word})

    blob_lc = blob.lower()
    for phrase, pid in _CLICHE_PHRASES.items():
        if phrase in blob_lc:
            out.append({'probe_id': pid,
                        'pattern': 'offshore_cliche',
                        'excerpt': phrase})

    for pat, pid in _OVERCLAIM_PATTERNS + _CONDESCENDING_PATTERNS:
        m = pat.search(blob)
        if m:
            out.append({'probe_id': pid,
                        'pattern': pat.pattern,
                        'excerpt': m.group(0)})

    # Word-count rule (style_guide.md "Max 120 words in cold outreach")
    body_words = len(re.findall(r'\w+', body))
    if body_words > 120:
        out.append({'probe_id': 'P-TONE-03',
                    'pattern': 'body_over_120_words',
                    'excerpt': f'{body_words} words'})
    return out


def _regeneration_instruction(violations: list[dict]) -> str:
    """Build a corrective instruction for the compose retry. Cites the
    specific violations so the model knows what to change rather than
    getting a generic 'do better' nudge."""
    bullets = []
    for v in violations:
        bullets.append(f"- fix {v['probe_id']}: "
                       f"'{v['excerpt']}' matches {v['pattern']}")
    return ('Your previous draft violated Tenacious style rules. '
            'Regenerate, fixing ONLY these:\n' + '\n'.join(bullets) +
            '\n\nKeep the rest. Do not repeat the violations.')


# --------------------------------------------------------------
# Orchestrator — dispatches V0/V1/V2/V3
# --------------------------------------------------------------

def compose_with_mechanism(brief: dict,
                           *,
                           variant: Variant,
                           compose_fn=None,
                           max_regens: int = 1) -> tuple[dict, MechanismTrace]:
    """Compose an email under the given mechanism variant.

    `compose_fn(brief) -> {'subject', 'html'}` is the LLM call. It is
    injected so the ablation runner can supply a deterministic stub for
    offline runs and the real `main_agent.compose_email` for live runs.
    If omitted, the real compose path is imported lazily.
    """
    if compose_fn is None:
        from main_agent import compose_email as compose_fn  # lazy

    trace = MechanismTrace(variant=variant)
    start = time.perf_counter()

    working_brief = brief
    if variant in ('V1', 'V3'):
        working_brief, applied = transform_brief_v1(brief)
        trace.v1_flags_applied = applied

    result = compose_fn(working_brief)
    trace.compose_calls += 1

    if variant in ('V2', 'V3'):
        for _ in range(max_regens + 1):
            violations = tone_judge(result.get('subject', ''),
                                    result.get('html', ''))
            if not violations:
                break
            trace.v2_judge_fired = True
            trace.v2_violations = violations
            trace.v2_regen_count += 1
            # Build a brief variant carrying the regen instruction so
            # the compose_fn sees it without needing an extra arg.
            regen_brief = dict(working_brief)
            regen_brief['_regeneration_instruction'] = \
                _regeneration_instruction(violations)
            result = compose_fn(regen_brief)
            trace.compose_calls += 1
        else:
            # Ran out of regens; record final state.
            trace.v2_violations = tone_judge(
                result.get('subject', ''), result.get('html', ''))

    trace.latency_s = round(time.perf_counter() - start, 3)
    return result, trace


# --------------------------------------------------------------
# Cost model — keeps the ablation honest when offline
# --------------------------------------------------------------

# $ per compose call for the dev-tier model (DeepSeek-Chat via
# OpenRouter). Keyed to the Act-I cost observation ($0.16 for 30 trials
# × ~2 calls per trial → ~$0.0027/call). Round to $0.003 for the
# mechanism ROI math; real invoice numbers override in the held-out
# ablation JSON.
COST_PER_COMPOSE_USD = 0.003


def estimated_cost(trace: MechanismTrace) -> float:
    """V0/V1 is 1 compose call. V2/V3 is 1 + n_regens compose calls
    (judge itself is deterministic, costs $0 in this implementation)."""
    return round(trace.compose_calls * COST_PER_COMPOSE_USD, 5)
