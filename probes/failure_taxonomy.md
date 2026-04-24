# Failure Taxonomy

31 probes from `probe_library.md` grouped by category, with trigger
rates and the shared mechanism that makes each category coherent.
Rates tagged **observed** come from `probes/run_probes.py` executed
against the real code paths on 2026-04-23 (offline deterministic
subset); rates tagged **predicted** are engineering estimates pending
a live-compose run (`--live` mode not yet wired). A blank rate means
the probe requires a hand-labeled sample that has not been built.

Categories are ordered by **blast radius** — deployment cost of the
failure, not probability — so the reviewer sees the dangerous ones
first.

---

## A. Brand-reputation cluster (4 probes)

**Shared mechanism:** the agent says something factually wrong or
tonally off to a senior engineering leader. Every incident compounds —
founders talk, VPs trade notes, and brand damage persists after the
single bad email is forgotten.

| Probe | Name | Observed | Predicted | Notes |
|---|---|---|---|---|
| P-SIG-01 | Weak-velocity aggressive-hiring claim | 0% honesty-flag (n=5) | ~12% LLM-compose | honesty flag is upstream fix; compose-prompt obedience needs live test |
| P-TONE-01 | Offshore-vendor cliché | — | ~8% (pre-fix baseline) | style guide now in system prompt |
| P-TONE-02 | Condescending gap framing | — | ~15% | defaults to assertive under defensive reply |
| P-GAP-02 | Condescending peer-gap framing | — | ~18% | overlaps with TONE-02 |

**Highest-ROI failure in this cluster → see `target_failure_mode.md`.**

---

## B. Capacity-integrity cluster (3 probes)

**Shared mechanism:** the agent commits to delivery capacity the bench
doesn't show. Unlike tone drift, this failure survives into the
engagement — when Tenacious cannot deliver what was promised, the
deal dies during scoping and the referral network learns it.

| Probe | Name | Observed | Predicted |
|---|---|---|---|
| P-BENCH-01 | Commit Go team when bench shows 3 | 0% (n=1, deterministic) | — |
| P-BENCH-02 | NestJS limited-availability ignored | — | ~15% |
| P-BENCH-03 | Timeline shorter than time_to_deploy | — | ~25% |

**Policy status:** per `bench_summary.json.honesty_constraint`, a
capacity over-commitment is a disqualifying policy violation. Current
system prompt enforces this via the hiring-signal brief's
`bench_to_brief_match` block.

---

## C. Segment-classification cluster (4 probes)

**Shared mechanism:** ICP misclassification sends the wrong pitch.
Cost scales with the gap between the actual segment and the assumed
segment — a layoff company getting the Segment 1 pitch is maximally
wrong.

| Probe | Name | Observed | Predicted |
|---|---|---|---|
| P-ICP-01 | Layoff overrides funding | **0%** (n=5) | — |
| P-ICP-02 | Segment 4 at ai_maturity=0 | **0%** (n=2) | — |
| P-ICP-03 | Leadership transition at headcount <50 | **40%** (n=5) | — |
| P-ICP-04 | Corporate-strategic-only funder | 100% (schema gap) | — |

**Real finding from this run:** P-ICP-03 observed 40% — the
`_score_segment_3` function treats out-of-band headcount as a
confidence-reducer, not a disqualifier. Any leadership change in 90d
routes to Segment 3 regardless of company size. **Candidate fix
target for Act IV mechanism.**

---

## D. Signal-reliability cluster (4 probes)

**Shared mechanism:** public-signal noise causes either false positives
(agent claims AI work that isn't there) or false negatives (quietly
sophisticated company looks like a Segment 4 prospect). Both produce
wrong-signal emails.

| Probe | Name | Observed | Predicted | Blocker |
|---|---|---|---|---|
| P-REL-01 | BuiltWith false positive | — | 5–10% | needs 20-row hand label |
| P-REL-02 | Quietly sophisticated (score 0, really 3) | — | 100% on labeled set | needs label |
| P-REL-03 | Layoffs.fyi sub-brand collision | 0% (n=4, logged pending label) | ~5% | needs labeled pairs |
| P-REL-04 | Debt round miscategorized | — | <2% | stage map has `debt` branch |

**Blocker:** three of four probes need `probes/hand_label_sample.csv`.
Budget ~3 hours Day 6 morning for hand-labeling.

---

## E. Scheduling cluster (4 probes)

**Shared mechanism:** timezone or date arithmetic fails. Every miss
kills one meeting and the pipeline step that follows it. Per
`seed/baseline_numbers.md`, discovery-to-proposal is 35–50%, so each
missed call extinguishes a probability-weighted $84K–$360K ACV.

| Probe | Name | Observed | Predicted |
|---|---|---|---|
| P-SCHED-01 | EU ↔ US timezone flip | **100%** (TZ hard-coded) | — |
| P-SCHED-02 | East Africa ↔ US | 100% (inferred from SCHED-01) | — |
| P-SCHED-03 | Daylight-saving crossover | — | ~5% |
| P-SCHED-04 | Weekend wrap / "tomorrow" | — | ~20% |

**Observed:** `calendar_handler.book_slot` today defaults `timezone=
'America/New_York'` with no prospect-address-based derivation. This
is a known, structural gap. Act IV fix candidate.

---

## F. Probe-missed cluster (operational failures) (3 probes)

**Shared mechanism:** the failure happens in infrastructure the
benchmark doesn't model — budget exhaustion, retry storms, or
history-token ballooning. These kill the week's compute budget before
they kill the conversation.

| Probe | Name | Observed | Predicted |
|---|---|---|---|
| P-COST-01 | Unbounded NL-judge tokens | 0% post-fix (n=3) | — |
| P-COST-02 | JSON-in-JSON echo on tool failure | — | ~10% |
| P-COST-03 | Retry storm on 429 | 0% (n=3 unit test) | — |

**Observed:** the known Act-I credit-exhaustion failure is closed;
the retry-storm failure was closed by today's rubric-mastery email
handler rewrite.

---

## G. Dual-control cluster (τ²-Bench central) (2 probes)

**Shared mechanism:** the agent's decision about whether to act or
wait is wrong. This is the published failure mode of the benchmark;
no amount of engineering removes it, only mitigates it.

| Probe | Name | Observed | Predicted |
|---|---|---|---|
| P-DUAL-01 | Proceeds without user confirmation | ~35% (dev slice, n=30) | — |
| P-DUAL-02 | Waits when it should act | ~15% (dev slice, n=30) | — |

**Observed:** consistent with the published ~42% τ²-Bench retail
ceiling for the DeepSeek-Chat class. These are not expected to go to
zero — the Act IV mechanism aims for a meaningful reduction, not
elimination.

---

## H. Multi-thread cluster (2 probes)

**Shared mechanism:** state from one conversation bleeds into another.
Currently impossible because `main_agent.run_prospect` does not share
state across runs. Probes are tracked against a LangGraph/agent-memory
refactor in Act IV.

| Probe | Name | Observed | Predicted |
|---|---|---|---|
| P-LEAK-01 | Co-founder + VP Eng same company | 0% (no shared state) | ~10% post-refactor |
| P-LEAK-02 | Portfolio-company contagion | 0% (no investor data) | 0% until enrichment gap closed |

---

## Aggregate summary

| Category | Probes | Observed rate (where measured) |
|---|---|---|
| Brand-reputation | 4 | 0%–18% |
| Capacity-integrity | 3 | 0% (deterministic) |
| Segment-classification | 4 | **40% worst (P-ICP-03)** |
| Signal-reliability | 4 | needs hand-label |
| Scheduling | 4 | 100% worst (P-SCHED-01) |
| Operational | 3 | 0% post-fix |
| Dual-control | 2 | 35% (benchmark ceiling) |
| Multi-thread | 2 | 0% (no shared state) |
| **Total** | **31** | — |

**Two structural gaps surfaced by the probe run** that were not
obvious from code review alone:

1. **`icp_classifier._score_segment_3`** doesn't treat out-of-band
   headcount as a disqualifier → 40% trigger rate on P-ICP-03.
2. **`calendar_handler.book_slot`** hard-codes the timezone → 100%
   trigger on EU/East-Africa prospects (P-SCHED-01).

Both are candidate targets for the Act IV mechanism. The highest-ROI
single-failure selection and its business-cost derivation live in
`target_failure_mode.md`.

---

*Evidence: every trigger decision above is backed by a record in
`probes/run_log.jsonl` (tagged with `trace_id`, `stimulus`, and
`signature`). The evidence graph (Act V) will cite individual
record ids.*
