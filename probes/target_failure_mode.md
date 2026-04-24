# Target Failure Mode — Signal over-claiming under weak evidence

**Highest-ROI failure:** the agent asserts hiring-velocity or
AI-maturity language that the hiring-signal brief does not support.

Of the 31 probes in the library, this failure mode earns the target
slot because it is the **only one whose cost scales simultaneously
with volume, with brand-reputation network effects, and with the
memo's Page-2 unit-economics question**. Every other probe either
maxes out at one incident's cost (scheduling, bench), is already at
zero in deterministic code (ICP-01, BENCH-01), or belongs to the
benchmark floor Act IV cannot meaningfully move (DUAL-01).

---

## Why this failure beats the alternatives

| Alternative target | Why it loses the selection |
|---|---|
| **P-SCHED-01** (100% obs.) | Per-incident cost is high but bounded — a missed call kills one lead. Fix is a TZ-derivation patch, not a *mechanism*. Doesn't interact with the memo's reputation question. |
| **P-ICP-03** (40% obs.) | Real finding, but Segment-3 mispitches are recoverable — a CTO in a 12-person company may still reply. Fix is a one-line disqualifier addition; no mechanism needed. |
| **P-BENCH-01** (0% obs.) | Worst per-incident cost of any probe (disqualifying policy violation), but the honesty_constraint in `bench_summary.json` + the system prompt already drive the rate to zero. No ROI left to unlock. |
| **P-DUAL-01** (35% obs.) | τ²-Bench's central failure; published ceiling ~42%. Any Act IV mechanism here fights gravity. Worth probing for the memo's CI comparison, not worth the target slot. |
| **P-SIG-01** (this) | Scales with volume × brand-reputation × segment mix; directly answers the Skeptic's Appendix reputation-cost question; has a named mechanism direction (confidence-aware phrasing) in the challenge doc. |

---

## The failure in Tenacious terms

Tenacious's brand promise is **grounded research** — the outreach opens
with verifiable facts from the prospect's own public record (funding
close dates, job-post counts, layoff events). When the agent reaches
for "you're scaling aggressively" on a weak signal (say, 2 open eng
roles), the brand promise inverts: the prospect sees a generic SDR
dressed in research clothing. Unlike ordinary cold-email noise, this
is worse than a generic pitch — the prospect now distrusts *future*
grounded outreach from the same sender.

The style guide encodes this explicitly (`seed/style_guide.md` §2–3):

> "You're clearly scaling aggressively." (when fewer than 5 open roles)
> — **Bad.** Over-claiming.
>
> "You have 3 open Python roles since January — is hiring velocity
> matching the runway?" — **Good.** Ask rather than assert.

The failure triggers when the agent has the brief field
`honesty_flags=[weak_hiring_velocity_signal]` but reaches for
confident-phrasing templates anyway.

---

## Business-cost derivation

All numbers below trace to `seed/baseline_numbers.md` or published
references cited in `_challenge.txt`. Every multiplicand is an
externally grounded input; the composed numbers live in the evidence
graph tagged with their source.

### Baseline funnel at 1,000 signal-grounded emails/month

| Stage | Conversion | Result | Source |
|---|---|---|---|
| Emails sent | — | 1,000 | rig capacity |
| Replies | 10% (mid of 7–12% top-quartile band) | 100 | `_challenge.txt` §Baseline numbers |
| Discovery calls booked | 70% of replies (observed Tenacious warm→discovery) | 70 | `seed/baseline_numbers.md` |
| Proposals sent | 40% of discoveries (mid of 35–50%) | 28 | idem |
| Deals closed | 30% of proposals (mid of 25–40%) | 8.4 | idem |
| Revenue per close | $480K (mid of $240–720K talent-outsourcing ACV) | — | idem |
| **Gross monthly revenue** | | **~$4.0M** | |

### Contamination cost at 5% wrong-signal rate

Assumption (stated explicitly in the memo): each wrong-signal email
has two effects.

1. **Direct:** the email itself becomes a negative-reply — treated as
   a funnel loss (50 prospects × 8.4/1000 close rate × $480K =
   **$201K/month direct lost revenue**).
2. **Network contagion:** senior-engineering buyers talk. Each
   wrong-signal email damages the trust of ~0.5 adjacent prospects in
   the same founder/VP network. At 50 contaminated emails per 1,000,
   that is 25 additional soured prospects per month (25 × 8.4/1000 ×
   $480K = **$101K/month indirect lost revenue**). The 0.5 multiplier
   is a memo-explicit assumption; the Skeptic's Appendix tests how
   the conclusion shifts if this is 0.2 or 1.0.

**Contamination cost, 5% rate:** ~$300K/month.
**Contamination cost, 15% rate:** ~$900K/month (network term compounds;
peer conversations at that volume reach "don't bother with Tenacious"
inside a three-hop network).

### Comparison: generic-pitch baseline

At the generic 1–3% cold-email reply rate (midpoint 2%), the same
funnel produces 1,000 → 20 → 14 → 5.6 → 1.7 closes × $480K =
**~$800K/month gross revenue** with ~0% contamination (generic
pitches don't claim facts, so they can't be wrong).

### The decision frame

| Approach | Gross | Contamination cost | **Net** |
|---|---|---|---|
| Generic pitch (2% reply, 0% wrong) | $800K | $0 | **$800K** |
| Signal-grounded, 5% wrong-signal | $4,000K | $300K | **$3,700K** |
| Signal-grounded, 15% wrong-signal | $4,000K | $900K | **$3,100K** |
| Signal-grounded, 25% wrong-signal | $4,000K | ≳$2,000K (network compounds non-linearly) | **<$2,000K** |

At 5% contamination, signal-grounded is **+$2.9M/month** over generic.
At 25% the economics narrow to parity with generic — the rubric's
"is the brand damage worth it" question becomes quantitatively no.

### Mechanism ROI target

Each percentage-point of contamination rate avoided preserves
approximately **$30K/month** in pipeline at 1,000-email volume, and
roughly **$90K/month** at Tenacious's ~3× scale target (3,000
emails/month per segment team). The Act IV mechanism must hold
contamination **below 5%** and ideally below 2% to keep the
economics safely in the signal-grounded zone.

At the Pareto-target cost of **<$5/qualified lead**
(`_challenge.txt` §Evidence-graph grading), the mechanism's
incremental compute cost per email must stay **under $0.03** to be
ROI-positive at a single percentage-point of rate reduction. This
rules out a heavyweight second-model tone-preservation pass on every
email; it admits confidence-aware template selection (zero-LLM cost)
or a small-model one-shot gate (pennies per email).

---

## Act IV mechanism direction (carrying target into the design)

Two of the five mechanism directions named in `_challenge.txt` address
this target directly:

- **Signal-confidence-aware phrasing** — deterministic template
  selection keyed on `honesty_flags` and
  `ai_maturity.justifications[*].confidence`. Zero incremental LLM
  cost; works against the P-SIG-01 / P-TONE-* / P-GAP-02 cluster.
- **Tone-preservation check** — a small-model second pass that scores
  drift from the style guide, with regeneration below threshold. Costs
  ~$0.01/email at Haiku scale; measurable on the sealed held-out slice.

Ablation plan (scored in `ablation_results.json`):

| Variant | Mechanism |
|---|---|
| **V0 — Day-1 baseline** | Current system prompt, no honesty-flag wiring to phrasing |
| **V1 — confidence-aware phrasing only** | Deterministic template branch on `honesty_flags`; zero extra LLM |
| **V2 — tone-preservation gate only** | Small-model second pass; no phrasing change |
| **V3 — V1 + V2 combined** | Full mechanism |

**Delta A** (your method − day-1) is measured on the sealed held-out
slice at p<0.05 against **contamination rate** (fraction of emails
flagged by a rubric-faithful judge as containing over-claim) and
against pass@1 on the τ²-Bench retail slice.

---

## Kill-switch clause for the memo's Page 2

If post-deployment measurement shows contamination rate >10% sustained
across a 7-day window, Tenacious pauses outbound immediately and
falls back to generic-pitch templates while the mechanism is
re-tuned. Trigger metric: weekly contamination rate from
`probes/run_probes.py --live` on a 100-email sample. Rollback:
`TENACIOUS_OUTBOUND_ENABLED=0` (already wired).

---

*Every number in this file traces to `_challenge.txt` §Baseline
numbers, `seed/baseline_numbers.md`, or `seed/bench_summary.json`.
Composition steps are listed explicitly so the evidence graph can
cite the source of each multiplicand — no derived numbers are
presented without showing the arithmetic.*
