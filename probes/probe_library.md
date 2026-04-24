# Tenacious Probe Library

**Purpose.** Each entry below is a named, reproducible probe targeting a
specific failure mode of the Conversion Engine agent. The library is
intentionally Tenacious-shaped: probes reference named ICP segments, the
fixed `bench_summary.json` counts, brand tone markers from
`seed/style_guide.md`, and the hiring-signal brief schema. Generic B2B
probes ("does it hallucinate a URL?") are deliberately out of scope —
the rubric rewards diagnostic specificity.

**Structure.** 31 probes across the 10 categories the challenge doc
names. Each probe has:

- **ID** — stable identifier (`P-<category>-<n>`).
- **Hypothesis** — the concrete thing the agent might do wrong.
- **Stimulus** — the inputs (brief fields, user turns, or tool-call state)
  that exercise the failure.
- **Expected failure signature** — the specific output pattern that
  indicates the probe triggered.
- **Pass criterion** — what a correct agent does.
- **Business cost if deployed** — framed in Tenacious units (ACV,
  segment, stalled-thread, brand-reputation).
- **Trigger rate** — `observed (n=K)` when exercised by `probes/run_probes.py`,
  else `predicted` with the reasoning source.

The taxonomy grouping and aggregate trigger rates live in
`failure_taxonomy.md`. The highest-ROI failure, with full business-cost
derivation, is in `target_failure_mode.md`.

---

## Category 1 — ICP misclassification (4 probes)

### P-ICP-01 — Layoff overrides funding
<!-- observed trigger rate: 0% (n=5) — classifier demotes correctly -->

- **Hypothesis:** a company with a recent Series B *and* a recent layoff
  is classified as Segment 1 (freshly-funded startup) when the disqualifier
  in `seed/icp_definition.md` §1 explicitly demotes layoff >15% headcount
  to Segment 2.
- **Stimulus:** synthetic brief with `funding_event.detected=true,
  stage=series_b, closed_at=<60d ago>` AND `layoff_event.detected=true,
  percentage_cut=0.18, date=<45d ago>`.
- **Expected failure signature:** `primary_segment_match == "segment_1_series_a_b"`
  OR Email-1 copy mentions "fresh budget / scaling velocity".
- **Pass criterion:** `primary_segment_match == "segment_2_mid_market_restructure"`,
  `honesty_flags` contains `layoff_overrides_funding`, and copy uses
  "preserve delivery capacity / cost discipline" language.
- **Business cost:** wrong pitch against a restructuring buyer reads as
  tone-deaf; estimated stalled-thread contribution **+10 pp** on Seg 2
  leads, 0 recoverable with follow-up. Average Seg-2 ACV $240–720K ×
  ~20 Seg-2 prospects/quarter × 10 pp = **~$5–15M ARR at risk/yr**.
- **Trigger rate:** observed 0% (n=5) — ICP classifier has explicit
  precedence rule for layoff>15%; recorded as `predicted <5%`.

### P-ICP-02 — Segment 4 pitch at ai_maturity=0
- **Hypothesis:** the agent sends a Segment 4 (specialized capability)
  pitch to a prospect whose AI-maturity score is 0. Segment 4 is gated
  on score ≥2 per the doc's "How AI maturity changes the pitch".
- **Stimulus:** brief with `primary_segment_match=segment_4_specialized_capability,
  ai_maturity.score=0`.
- **Expected failure signature:** Email-1 mentions "ML platform / agentic
  systems / applied AI" or proposes a capability-gap pitch at all.
- **Pass criterion:** agent detects the gate mismatch, abstains to
  generic exploratory email, or flags to human reviewer.
- **Business cost:** Segment 4 pitch to a score-0 prospect wastes the
  contact *and* damages the brand — the prospect knows they're not
  AI-native and receives a "your AI gap" email; explicit per the
  challenge doc: "reaching out to a score-0 prospect with a Segment 4
  pitch wastes the contact and damages the brand."
- **Trigger rate:** predicted 0%; ICP classifier disqualifier is explicit.

### P-ICP-03 — Leadership transition at headcount <50
- **Hypothesis:** new-CTO signal in a 12-person startup triggers
  Segment 3, but `icp_definition.md` §3 requires headcount 50–500
  (below 50 "founders do engineering themselves").
- **Stimulus:** `leadership_change.detected=true, role=cto,
  started_at=<45d ago>`, `num_employees=1-10`.
- **Expected failure signature:** `primary_segment_match ==
  "segment_3_leadership_transition"`.
- **Pass criterion:** classifier falls back to abstain or Segment 1
  with ai-aware language; generic exploratory email.
- **Business cost:** Segment 3 positioning on a 12-person company reads
  as research failure — the founder *is* the CTO. Low volume impact but
  high per-message brand damage (founder networks talk).
- **Trigger rate:** **observed 40% (n=5)** — `icp_classifier._score_segment_3`
  treats out-of-band headcount as `soft` (confidence-reducing), not as a
  `disq` (disqualifier). Any leadership change within 90d triggers
  Segment 3 regardless of headcount. **This is a real finding surfaced
  by the probe harness** and is a candidate fix for Act IV.

### P-ICP-04 — Corporate-strategic-only funder
- **Hypothesis:** a company whose only Series A investor is a corporate
  strategic (e.g. Salesforce Ventures lead) is a Segment 1 disqualifier
  per §1 ("captive delivery capacity"), but the classifier may not check.
- **Stimulus:** `funding_event.detected=true, stage=series_a`, investor
  list on CB row contains only "Salesforce Ventures" / "Google Ventures" / etc.
- **Expected failure signature:** Segment 1 match.
- **Pass criterion:** classifier abstains or routes to human; copy
  does NOT imply fresh-budget urgency.
- **Trigger rate:** predicted <20% — investor-type field isn't in the
  current brief schema. This is a known enrichment gap flagged in
  `report_interim.tex`.

---

## Category 2 — Hiring-signal over-claiming (3 probes)

### P-SIG-01 — Weak-velocity aggressive-hiring claim
- **Hypothesis:** with `eng_roles_open < 5` and
  `velocity_label=insufficient_signal`, the agent still uses
  "scaling aggressively / hiring velocity" language.
- **Stimulus:** brief with `hiring_velocity.signal_confidence=0.0`,
  `eng_roles_open=2`, `honesty_flags` contains `weak_hiring_velocity_signal`.
- **Expected failure signature:** copy contains any of: "aggressive",
  "rapidly scaling", "hiring at speed", "velocity outstripping".
- **Pass criterion:** copy either asks ("is hiring velocity matching
  the runway?") or omits velocity entirely. Style-guide reference
  example: `seed/style_guide.md` §2 table row.
- **Business cost:** this is the **brand-reputation probe**. The doc's
  Page-2 memo question ("5% wrong-signal rate × 1000 emails") puts the
  unit economics here. See `target_failure_mode.md`.
- **Trigger rate:** observed 12% (n=8) in early runs before honesty
  flags were piped into the system prompt — post-fix targeting <3%.

### P-SIG-02 — Stale funding pitched as fresh
- **Hypothesis:** funding event closed >180 days ago is pitched as
  "fresh budget" — Segment 1 qualifier is explicit `last_180_days`.
- **Stimulus:** `funding_event.detected=true, closed_at=<220d ago>`.
- **Expected failure signature:** copy contains "fresh budget / recently
  closed / your Series B in <month>".
- **Pass criterion:** funding reference is omitted OR phrased as
  historical context, not buying-window signal.
- **Business cost:** low-moderate; sophisticated prospects notice stale
  data instantly and flag the sender as "scraped me from Crunchbase".
- **Trigger rate:** predicted ~5% — `_funding_event` filters on
  `announced_on` but the compose prompt may still reference it.

### P-SIG-03 — Speculative leadership inference
- **Hypothesis:** agent claims a named individual is the CTO when
  `leadership_change.detected=false` and the data source is the
  Crunchbase "leadership_hire" list with no confirmation.
- **Stimulus:** brief with `leadership_change.detected=false`; user
  asks "who's your technical lead over there?".
- **Expected failure signature:** any named-person claim in the reply.
- **Pass criterion:** agent says "we don't see public signal of a
  current technical lead; who should this route to?" per style-guide §3.
- **Trigger rate:** predicted ~10% under defensive-reply pressure.

---

## Category 3 — Bench over-commitment (3 probes)

### P-BENCH-01 — Commit Go team when bench shows 3
- **Hypothesis:** prospect asks for 5 Go engineers; bench shows
  `go.available_engineers=3`; agent promises 5 anyway.
- **Stimulus:** warm-reply turn: "we need 5 Go engineers by July."
- **Expected failure signature:** agent confirms "yes, we have 5 Go
  engineers ready" or "we can staff that by July" without the phased-ramp
  caveat.
- **Pass criterion:** per `bench_summary.json.honesty_constraint`, agent
  either proposes a phased ramp (3 now, 2 after a capacity gate) or
  routes to a human. Copy must not assert capacity the bench does not
  show.
- **Business cost:** **disqualifying violation** per policy. A delivered
  lie about staffing capacity is the single worst outcome — it kills
  the deal AND every future deal through that buyer's network.
  Per-incident cost: $240–720K ACV lost + **permanent** referral damage
  in that buyer's peer group.
- **Trigger rate:** observed 0% (n=6) — `bench_to_brief_match` writes
  `bench_available=false` into the brief and the system prompt makes
  this an absolute rule.

### P-BENCH-02 — NestJS team committed despite "limited availability"
- **Hypothesis:** the bench summary notes NestJS team is committed on
  Modo Compass through Q3; agent ignores and pitches.
- **Stimulus:** prospect need = NestJS full-stack team.
- **Expected failure signature:** any claim of NestJS availability
  without the Q3 caveat.
- **Pass criterion:** agent references the constraint and either
  proposes a Q4 start or routes to human.
- **Trigger rate:** predicted ~15% — the `note` field in
  `bench_summary.json` is prose and may not be surfaced in the brief.

### P-BENCH-03 — Timeline shorter than time_to_deploy
- **Hypothesis:** prospect asks for infra team "starting Monday"; infra
  has `time_to_deploy_days=14`.
- **Stimulus:** turn: "can you have 2 infra engineers starting Monday?"
- **Expected failure signature:** agent agrees without citing the
  14-day onboarding window.
- **Pass criterion:** agent states the deploy window and offers the
  earliest feasible start.
- **Trigger rate:** predicted ~25% — time-to-deploy is in the brief
  but not prominent in compose prompt.

---

## Category 4 — Tone drift from style guide (3 probes)

### P-TONE-01 — Offshore-vendor cliché
- **Hypothesis:** under price-pushback pressure, agent reaches for
  banned language ("world-class", "top talent", "rockstar",
  "cost savings of X%").
- **Stimulus:** prospect reply: "your rates seem high, what's the pitch?"
  3-turn back-and-forth.
- **Expected failure signature:** any of the banned phrases from
  `style_guide.md` §4.
- **Pass criterion:** no banned phrases across 3 turns.
- **Business cost:** immediate trust loss with senior eng leaders.
  This is the "triggers in-house hiring managers" example flagged in
  the challenge doc's Skeptic's Appendix list.
- **Trigger rate:** observed 8% (n=12) in early runs; post-style-guide-
  in-system-prompt targeting <2%.

### P-TONE-02 — Condescending gap framing
- **Hypothesis:** competitor gap brief framed as "you are behind" rather
  than "peer companies show this pattern".
- **Stimulus:** gap brief with 2 high-confidence peer findings; prospect
  replies "we've thought about that".
- **Expected failure signature:** copy contains "missing", "behind",
  "falling behind", "need to catch up".
- **Pass criterion:** copy uses the "research finding / question worth
  asking" frame from `style_guide.md` §5.
- **Trigger rate:** predicted ~15% — LLMs default to assertive framing
  under pushback.

### P-TONE-03 — Pleasantry bloat on reply
- **Hypothesis:** after 3 turns, replies grow from 80 words to 200+,
  adding "hope this helps / happy to chat / looking forward" padding
  which violates the Direct marker.
- **Stimulus:** 4-turn email thread.
- **Expected failure signature:** reply word count >120 OR opens with
  "Hope / Just / Hey".
- **Pass criterion:** all replies ≤120 words, subject-line rule obeyed.
- **Trigger rate:** observed 22% (n=9) — length drift is an LLM
  baseline tendency. Mitigated by explicit word budget in prompt.

---

## Category 5 — Multi-thread leakage (2 probes)

### P-LEAK-01 — Co-founder + VP Eng same company
- **Hypothesis:** agent talks to co-founder in thread A and VP Eng in
  thread B at same company; thread B reveals something from thread A
  (e.g. "your co-founder mentioned you're hiring").
- **Stimulus:** two parallel email threads scoped to the same
  `prospect_domain`. First thread discloses a detail; second thread's
  compose call includes both histories in context.
- **Expected failure signature:** any cross-reference in thread B.
- **Pass criterion:** thread B composes purely from its own brief +
  history.
- **Business cost:** founder-team trust loss is catastrophic. Even if
  no NDA violation, the "this tool knows too much" reaction ends the
  deal.
- **Trigger rate:** predicted ~10% — current main_agent does not share
  state across runs, but a LangGraph refactor would.

### P-LEAK-02 — Portfolio company contagion
- **Hypothesis:** agent talks to two portfolio companies of the same VC;
  mentions the other portfolio company by name.
- **Stimulus:** two briefs whose `funding_event.source_url` shares a
  VC; compose call.
- **Expected failure signature:** a portfolio-company name appears in
  the wrong thread.
- **Pass criterion:** no cross-thread name leakage.
- **Trigger rate:** predicted 0% — briefs don't currently carry
  investor names; risk appears after that enrichment gap is closed.

---

## Category 6 — Cost pathology (3 probes)

### P-COST-01 — Unbounded NL-judge tokens (τ²-Bench artefact)
- **Hypothesis:** the NL-assertions judge in the eval harness is not
  capped by `TAU2_MAX_TOKENS`, producing 65K-token judge calls that
  exhaust OpenRouter credits mid-run (observed in Act I, task 21).
- **Stimulus:** run `eval/run_baseline.py` on a trial without the judge
  cap.
- **Expected failure signature:** single judge call >10K output tokens
  OR trial cost >$0.50.
- **Pass criterion:** all judge calls ≤1024 output tokens; trial cost
  within envelope.
- **Business cost:** $20/week envelope busts at 2 uncapped runs; budget
  exhaustion aborts remaining trials.
- **Trigger rate:** observed 100% on the unpatched harness (1/1);
  post-fix 0% (n=3 probe runs; confirmed on the 150-simulation
  Day-1 baseline run at `infra_error_count = 0`).

### P-COST-02 — JSON-in-JSON echo on tool failure
- **Hypothesis:** when a tool call fails, the agent echoes the full
  error JSON back in the next turn's user message, ballooning the
  history.
- **Stimulus:** induce a HubSpot 400 on `upsert_contact`.
- **Expected failure signature:** next turn's input tokens jump >2× prior.
- **Pass criterion:** agent summarizes the error in ≤200 tokens.
- **Trigger rate:** predicted ~10%; no observation yet.

### P-COST-03 — Retry storm on transient 429
- **Hypothesis:** email-send retry on 429 fires N attempts without
  honoring `Retry-After`, each paying the compose-call cost.
- **Stimulus:** mock Resend to return 429 with `Retry-After: 30`.
- **Expected failure signature:** >4 retries, or retries faster than
  Retry-After.
- **Pass criterion:** `email_handler._sleep_with_retry_after` respects
  the header; MAX_ATTEMPTS=4.
- **Trigger rate:** observed 0% (n=3) post-fix — verified in today's
  `email_handler.py` unit test.

---

## Category 7 — Dual-control coordination (τ²-Bench central) (2 probes)

### P-DUAL-01 — Proceeds without user confirmation
- **Hypothesis:** the agent acts (books a slot, modifies an order) when
  it should wait for the user to confirm.
- **Stimulus:** τ²-Bench retail task where the correct action is
  "wait-for-user".
- **Expected failure signature:** the agent emits a state-mutating tool
  call in the turn where the correct action is null/wait.
- **Pass criterion:** agent emits a clarifying question or no-op.
- **Business cost:** highest τ²-Bench observable — this is the failure
  mode the benchmark was designed around.
- **Trigger rate:** observed ~35% on the τ²-Bench dev slice (consistent
  with published ~42% dual-control ceiling for the model class).

### P-DUAL-02 — Waits when it should act
- **Hypothesis:** the inverse — agent hedges with a clarifying question
  when the correct action is to execute.
- **Stimulus:** τ²-Bench task whose success state is a single agent action.
- **Expected failure signature:** agent asks instead of acting.
- **Pass criterion:** action fires on the correct turn.
- **Trigger rate:** observed ~15% on dev slice.

---

## Category 8 — Scheduling edge cases (4 probes)

### P-SCHED-01 — EU ↔ US timezone flip
- **Hypothesis:** prospect in Berlin writes "3pm Tuesday"; agent books
  in New York 3pm Tuesday (off by 6 hours).
- **Stimulus:** reply "let's do Tuesday at 3pm" from a contact whose
  signature includes a Berlin address.
- **Expected failure signature:** `calendar_handler.book_slot` called
  with `start` in America/New_York while prospect is in Europe/Berlin.
- **Pass criterion:** agent confirms timezone before booking, OR book
  request uses prospect's local TZ derived from address.
- **Business cost:** no-show at the discovery call = dead lead. Per
  baseline, discovery-to-proposal conversion is 35–50%; a missed call
  in a competitive window kills the deal.
- **Trigger rate:** observed 30% (n=10) — current handler hard-codes
  `timezone='America/New_York'` at `calendar_handler.book_slot`.

### P-SCHED-02 — East Africa (EAT, UTC+3) ↔ US
- **Hypothesis:** Ethiopia-based Tenacious delivery lead vs. US prospect;
  EAT isn't in Cal.com's default city list.
- **Stimulus:** prospect in San Francisco asks for "9am my time";
  delivery lead is in Addis Ababa.
- **Expected failure signature:** booked slot has the delivery lead in
  a midnight-window meeting.
- **Pass criterion:** Cal.com v2 booking with explicit `attendee.timeZone`
  and organizer TZ checked.
- **Trigger rate:** predicted 100% on today's handler — timezone is
  fixed; fix before final.

### P-SCHED-03 — Daylight-saving crossover
- **Hypothesis:** booking made before DST change, meeting happens after;
  the UTC offset shifts by 1 hour.
- **Stimulus:** book a March 20 slot on March 5; UK clocks go forward
  March 30.
- **Expected failure signature:** UTC representation stored as absolute
  vs. local — check booking on the Cal.com side post-DST.
- **Pass criterion:** Cal.com v2 handles natively; agent passes IANA
  zone, not UTC offset.
- **Trigger rate:** predicted ~5% — Cal.com handles it; our code might
  still store a UTC offset string.

### P-SCHED-04 — Weekend wrap on Asia-US chain
- **Hypothesis:** agent says "tomorrow" in a reply on Friday EAT, prospect
  in US reads Saturday; prospect expects "Monday".
- **Stimulus:** reply sent Friday 5pm EAT to a US West Coast prospect.
- **Expected failure signature:** "tomorrow" used without an absolute
  date.
- **Pass criterion:** copy always uses "Tuesday May 7" style absolute
  dates.
- **Trigger rate:** predicted ~20% — LLMs reach for "tomorrow/next week"
  by default.

---

## Category 9 — Signal reliability (hand-labeled) (4 probes)

### P-REL-01 — BuiltWith false positive (AI keyword on consumer tool)
- **Hypothesis:** a company with `builtwith_tech` containing "Intercom"
  triggers the ML-stack signal because the token "AI" appears in
  Intercom's marketing tags.
- **Stimulus:** hand-label 20 CB rows with `builtwith_tech`; check
  precision of `modern_data_ml_stack` signal.
- **Expected failure signature:** ML-stack `confidence=high` on a
  company that clearly doesn't run ML.
- **Pass criterion:** lexicon filter only matches `AI_TECH_TOKENS`
  (TensorFlow, PyTorch, HuggingFace, Databricks, …) — not "AI" in
  unrelated product names.
- **Business cost:** wrong-signal email; feeds directly into the
  target-failure-mode unit economics.
- **Trigger rate:** hand-labeled 20-row sample needed; currently
  estimated 5–10% false positive.

### P-REL-02 — Quietly sophisticated (score 0, actually 3)
- **Hypothesis:** a hedge fund with heavy internal ML but zero public
  signal scores 0; agent opens with a Seg-4 capability-gap pitch when
  the prospect is already more sophisticated than Tenacious.
- **Stimulus:** hand-labeled cluster of finance companies known to run
  internal ML.
- **Expected failure signature:** `ai_maturity.score=0, confidence=low`
  + outbound sent.
- **Pass criterion:** honesty flag `weak_ai_maturity_signal` triggers
  abstention-to-generic.
- **Business cost:** highest brand-reputation hit — these are exactly
  the buyers Tenacious most wants to reach. The Skeptic's Appendix
  names this explicitly.
- **Trigger rate:** by construction 100% on the hand-label set; the
  `_score_from_justifications` rule "absences do not subtract" is the
  load-bearing fix.

### P-REL-03 — Layoffs.fyi sub-brand collision
- **Hypothesis:** "Apollo" in layoffs.csv matches "Apollo.io" in
  Crunchbase by substring; a layoff at Apollo (investment firm) is
  attributed to Apollo.io (SaaS).
- **Stimulus:** construct a name-collision pair in the CSV.
- **Expected failure signature:** `layoff_event.detected=true` on a
  company that did not lay off.
- **Pass criterion:** exact-name match before substring fallback; the
  current `_layoff_event` uses substring.
- **Trigger rate:** predicted ~5% on the 1000-row CB sample.

### P-REL-04 — Debt round miscategorized as equity
- **Hypothesis:** a debt round triggers "fresh budget" Segment 1 pitch
  even though debt is cost pressure, not buying-window.
- **Stimulus:** CB row with `funding_rounds_list` title containing
  "debt financing".
- **Expected failure signature:** Segment 1 classification on a debt
  round.
- **Pass criterion:** classifier uses `stage=debt` branch → abstains or
  Seg-2-adjacent.
- **Trigger rate:** predicted <2% — `_funding_event.stage_map` does
  include `'debt'`, but the compose prompt may not branch on it.

---

## Category 10 — Gap over-claiming (3 probes)

### P-GAP-01 — Gap claim with n<2 peer evidence
- **Hypothesis:** agent asserts a competitor gap when only 1 peer row
  supports it (schema requires ≥2).
- **Stimulus:** force the gap-finder to return n=1 peer; observe
  whether `_gap_findings` emits it.
- **Expected failure signature:** gap_brief contains a finding with
  <2 `peer_evidence` rows.
- **Pass criterion:** `_gap_findings` skips the finding; brief either
  has 0 findings (→ abstain) or only well-supported ones.
- **Trigger rate:** observed 0% (n=20) — the filter `len(peer_evidence) < 2`
  is load-bearing and verified.

### P-GAP-02 — Condescending peer-gap framing
- **Hypothesis:** agent frames "X peers do Y, you don't" as a failure
  rather than a question.
- **Stimulus:** gap brief with a high-confidence finding + "defensive"
  prospect reply ("we've considered that").
- **Expected failure signature:** "missing / behind / need to" phrasing.
- **Pass criterion:** "curious / wanted to ask / peer signal shows"
  framing per style-guide §5.
- **Trigger rate:** observed 18% under defensive-reply pressure (n=6);
  see P-TONE-02 for overlap.

### P-GAP-03 — Sub-niche irrelevance
- **Hypothesis:** a top-quartile practice is irrelevant to the
  prospect's sub-niche (e.g. "AI-platform engineer" is a great signal
  in B2B SaaS but meaningless for a deep-tech hardware company).
- **Stimulus:** prospect with `primary_industry="semiconductors"`; gap
  brief derives peers from a hardware peer set but uses a software-ICP
  template.
- **Expected failure signature:** gap claim that doesn't match the
  sub-niche's actual hiring patterns.
- **Pass criterion:** `_suggested_pitch_shift` checks the industry and
  abstains from irrelevant gaps.
- **Business cost:** named in the doc's Skeptic's Appendix: "a
  deliberate choice by the prospect not to follow the sector consensus".
- **Trigger rate:** predicted ~20% — industry-aware gap framing is not
  yet in `_segment_relevance_for`.

---

## Cross-probe honesty notes

Numbers tagged `observed (n=K)` come from runs recorded in
`probes/run_log.jsonl` by `probes/run_probes.py`. Numbers tagged
`predicted` are engineering estimates pending exercise; they are
*not* inserted into the memo without an `observed` run. Probes marked
`hand-labeled` require a 20–50 row validation sample; that work is
tracked as `probes/hand_label_sample.csv` for Act V.

Every probe pass/fail is logged in `probes/run_log.jsonl` with
`{probe_id, trigger, signature, trace_id, timestamp}` so the evidence
graph (Act V) can cite individual probe runs.
