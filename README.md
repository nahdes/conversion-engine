# Conversion Engine — Week 10 (Tenacious Consulting)

An automated lead-generation and conversion system that finds prospective
clients from public data, qualifies them against a real intent signal,
runs a nurture sequence, and books discovery calls.

## Team

- **Nahom** — engineer / trainee (10 Academy TRP1 Week 10)

## Architecture

```
  Crunchbase CSV   layoffs.fyi CSV   public careers pages
          \              |                   /
           \             |                  /
            +------------v-----------------+
            |      enrichment pipeline     |   agent/enrichment.py
            |  funding · layoffs · jobs ·  |
            |  leadership · AI maturity    |
            +------+----------------+------+
                   |                |
       hiring_signal_brief.json     |  competitor_gap_brief.json
                   |                |
            +------v----------------v------+
            |     LLM compose (OpenRouter) |   agent/main_agent.py
            |     signal-grounded email    |
            +------+-------+---------------+
                   |       |
                Resend   HubSpot upsert + note
          (staff sink    (agent/hubspot_handler.py)
           by default)
                   |
          reply webhook -> FastAPI     agent/server.py
                   |
         +---------v---------+
         |  Africa's Talking | warm-lead SMS
         |  Cal.com booking  | discovery call
         +-------------------+
                   |
              Langfuse (every step traced, cost + latency attached)
```

Act I (eval) and Act II (production) are independent:

- **Act I — `eval/run_baseline.py`**: τ²-Bench retail reproduction, logs to
  Langfuse, writes `eval/score_log.json` (Wilson 95% CI) and
  `eval/trace_log.jsonl` (per-task trajectories).
- **Act II — `agent/main_agent.py`**: end-to-end prospect flow:
  enrichment → LLM compose → HubSpot upsert → email send → Cal.com booking.

## Kill-switch and outbound routing

This system does **not** send messages to real Tenacious prospects. All
outbound email, SMS, and calendar invitations are routed to the staff-
controlled synthetic sink during the challenge week, per the TRP1 data-
handling policy.

Routing is gated by the `TENACIOUS_OUTBOUND_ENABLED` environment variable
(name fixed by `policy/data_handling_policy.md` Rule 5):

| State                          | Destination                                       | When to use                                                                    |
| ------------------------------ | ------------------------------------------------- | ------------------------------------------------------------------------------ |
| **Unset (default)**            | `STAFF_SINK_EMAIL` / `STAFF_SINK_SMS` from `.env` | Always during the challenge week                                               |
| `TENACIOUS_OUTBOUND_ENABLED=1` | The real recipient address                        | Only after program staff **and** Tenacious executive team **written** approval |

To pause live outbound immediately: unset the variable or set
`TENACIOUS_OUTBOUND_ENABLED=0`. Every outbound handler in `agent/*.py`
consults this flag via `main_agent.resolve_destination` and falls back
to the sink when unset. Bypassing this gate in code — even for a single
test message — is a policy violation regardless of outcome (TRP1
data-handling policy Rule 5).

All Tenacious-branded content (email copy, call scripts, pricing
quotations) produced by this system is marked `metadata.status = "draft"`
until a human reviewer approves it.

## Setup

```bash
git clone https://github.com/nahdes/conversion-engine.git
cd conversion-engine

python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
# source .venv/bin/activate

pip install -r agent/requirements.txt
# Optional (enrichment live crawl; interim ships with a stub):
# playwright install chromium

cp .env.example .env    # fill in your keys — see `.env` keys below
```

## Environment variables

| Variable                                                                               | Purpose                                                           |
| -------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| `OPENROUTER_API_KEY`, `DEV_MODEL`, `TAU2_MODEL`, `TAU2_TEMPERATURE`, `TAU2_MAX_TOKENS` | LLM (dev tier) + τ²-Bench pinned settings                         |
| `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_HOST`                          | Observability                                                     |
| `RESEND_API_KEY`, `FROM_EMAIL`                                                         | Email                                                             |
| `AT_API_KEY`, `AT_USERNAME`, `AT_SHORTCODE`                                            | Africa's Talking SMS                                              |
| `HUBSPOT_TOKEN`                                                                        | HubSpot private app token                                         |
| `CALCOM_API_KEY`, `CALCOM_EVENT_TYPE_ID`                                               | Cal.com booking                                                   |
| `STAFF_SINK_EMAIL`, `STAFF_SINK_SMS`                                                   | Sink addresses used when `TENACIOUS_OUTBOUND_ENABLED` is unset    |
| `TENACIOUS_OUTBOUND_ENABLED`                                                           | **Unset by default.** Set to `1` only with staff + exec approval. |

## Act I — Eval

```bash
python eval/run_baseline.py
# → eval/score_log.json (95% CI)
# → eval/trace_log.jsonl (per-task trajectories)
```

Pinned settings in `.env`: `TAU2_MODEL=deepseek/deepseek-chat`,
`TAU2_TEMPERATURE=0.0`, `TAU2_MAX_TOKENS=1024`. See `baseline.md` for
results and honest reproduction notes.

## Act II — Production

```bash
# Start the webhook server (reply handling for email + SMS)
python agent/server.py

# In another terminal: run one synthetic prospect end-to-end
python -c "
from agent.main_agent import run_prospect
run_prospect(
    company='Stripe',
    email='sink@example.com',        # staff sink by default
    careers_url='https://stripe.com/jobs/search',
)
"
```

Produces a hiring signal brief and (if competitors are named) a competitor
gap brief under `data/briefs/`, logs every step to Langfuse, and upserts a
HubSpot contact with enrichment timestamp.

## Repo layout

```
conversion-engine/
├── agent/              Act II handlers + orchestrator + Act IV mechanism
│   ├── email_handler.py   Resend outbound + reply webhook parser
│   ├── sms_handler.py     Africa's Talking SMS + STOP keywords + warm gate
│   ├── hubspot_handler.py Contact upsert + engagement note
│   ├── hubspot_mcp.py     MCP adapter (MCP-first, REST fallback)
│   ├── calendar_handler.py Cal.com slot lookup + booking + HubSpot sync
│   ├── enrichment.py      Crunchbase + layoffs + jobs + AI maturity
│   ├── icp_classifier.py  4-segment + abstain classifier (5-rule precedence)
│   ├── mechanism.py       Act IV V1/V2/V3 compose guards
│   ├── main_agent.py      Orchestrator: enrich → compose → write → send
│   ├── server.py          FastAPI webhook receiver
│   └── requirements.txt
├── eval/               Act I τ²-Bench reproduction + Act IV ablation
│   ├── run_baseline.py       τ² harness + Langfuse logger + Wilson CI
│   ├── tenacious_holdout.py  Act IV ablation harness (5 conditions × 20 tasks)
│   ├── score_log.json        5 trials × 30 tasks = 150 sims, pass@1 = 0.7267
│   ├── trace_log.jsonl       150 per-simulation rows
│   ├── ablation_results.json Delta A + Delta B stat tests
│   ├── held_out_traces.jsonl 100 per-task ablation traces
│   ├── method.md             Act IV mechanism writeup
│   └── tau2-bench/           Upstream benchmark (sierra-research/tau2-bench)
├── probes/             Act III probe library (31 probes across 10 categories)
│   ├── probe_library.md
│   ├── failure_taxonomy.md
│   ├── target_failure_mode.md
│   ├── run_probes.py
│   └── run_log.jsonl
├── data/
│   ├── crunchbase-companies-information.csv
│   ├── layoffs.csv
│   └── briefs/            Generated hiring_signal_brief_*.json + *_abstain.json
├── policy/             Data-handling policy + acknowledgement
├── schemas/            JSON schemas for hiring + gap briefs
├── seed/               Tenacious reference materials (icp, style guide, pricing)
├── baseline.md         Act I reproduction notes
├── evidence_graph.json 21 memo claims → trace IDs / invoice lines / sources
├── invoice_summary.json Cost attribution + cost-per-qualified-lead math
├── PROJECT_WALKTHROUGH.md Single-source project guide (md version)
├── project_walkthrough.tex / .pdf Same guide compiled for submission
├── DEMO_RUNBOOK.md     8-minute video recording script
├── demo_stage.py       Demo harness — stages 3 briefs + 5 runners
└── README.md
```

## Reproducing the ablations

```bash
# Offline dry-run (deterministic stubs, no API key, ~1 s):
python eval/tenacious_holdout.py --dry-run \
    --slice held_out --conditions V0 V1 V2 V3 AutoAgent

# Live LLM run (DeepSeek via OpenRouter, ~$0.60, ~5–8 min):
python eval/tenacious_holdout.py \
    --slice held_out --conditions V0 V1 V2 V3 AutoAgent

# Stat test only (reads saved eval/ablation_results.json):
python eval/tenacious_holdout.py --stat-test V3 V0 --metric contamination_rate
```

## Writeups index

| File | What it covers |
|---|---|
| `baseline.md` | τ²-Bench reproduction notes |
| `eval/method.md` | Act IV mechanism + ablation design |
| `probes/probe_library.md` | 31 probes across 10 rubric categories |
| `probes/failure_taxonomy.md` | Probes grouped by category with trigger rates |
| `probes/target_failure_mode.md` | P-SIG-01 selection + business-cost derivation |
| `report_interim.pdf` | Interim report (submitted Wednesday) |
| `PROJECT_WALKTHROUGH.md` + `.pdf` | Comprehensive end-to-end walkthrough |
| `DEMO_RUNBOOK.md` | 8-minute video recording script |
| `memo.pdf` | 2-page final decision memo *(pending)* |

## Budget

Dev-tier target (Days 1–4): < **$4** LLM spend via OpenRouter.
Week target: < **$20** total.

## Handoff notes — known limitations and next steps

Inheritor: the shortest path from cloning to a live demo is `pip install -r
agent/requirements.txt` + `python -m playwright install chromium` + fill in
`.env`. The items below are the concrete rough edges a successor will hit
in roughly the order they will hit them.

### Known limitations

1. **60-day job-post history is cold-start on first run.** The velocity
   window (`agent/enrichment.py::_hiring_velocity`) reads a per-prospect
   snapshot store at `data/job_history/<slug>.json`. A prospect scraped
   for the very first time has `velocity_label=insufficient_signal`
   until a second scrape ≥45 days later closes the window. Fix: run a
   nightly scrape sweep so the history accumulates before any prospect
   is pitched.
2. **Playwright live crawl requires `chromium` on disk.** `pip install`
   alone does not download the browser; run `python -m playwright
   install chromium` after `pip install`. Without it, `scrape_job_posts`
   returns `status=error` and every brief carries
   `honesty_flags=["weak_hiring_velocity_signal"]`.
3. **GitHub-org activity signal is stubbed.** `agent/enrichment.py::
   _maturity_justifications` emits a justification with
   `signal=github_org_activity, status='not wired'`. The scoring
   function treats this correctly (contributes nothing) but a
   successor wiring it up should: (a) map CB `linkedin_url` → inferred
   GitHub org slug, (b) call `GET /orgs/<slug>/repos?sort=pushed` with a
   `GITHUB_TOKEN`, (c) count repos with `pushed_at` inside the last 90
   days + `language` ∈ {Python, Jupyter}.
4. **Press / investor-letter scrape is stubbed.** `executive_commentary`
   and `strategic_communications` are currently inferred from
   Crunchbase description text. A successor should add a lightweight
   scrape of `company.com/press`, `company.com/blog`, and RSS where
   available, keyed on AI-language token matches.
5. **Gap brief abstains on most random Crunchbase rows.** Not a bug —
   `data/briefs/competitor_gap_brief_*.abstain.json` is the honesty
   posture when the peer pool has <5 rows with valid headcount_band.
   `_cheap_competitor_brief` in `agent/enrichment.py` is the peer
   pipeline; add an active peer-search (CB industry filter + min
   employees) to raise coverage.
6. **HubSpot custom properties must exist in the portal.** The router
   writes `tenacious_channel_state`, `tenacious_status`,
   `sms_unsubscribed`, `email_unsubscribed`, `warm_channel_open`,
   `calcom_booking_uid`, `last_email_sent_at`, `last_email_reply_at`,
   `last_sms_inbound_at`, `meeting_url`, `opt_out_channel`,
   `opt_out_reason`. The REST fallback retries once without unknown
   properties (see `agent/hubspot_handler.py::_upsert_via_rest`) but
   audit notes become the primary evidence until a portal admin adds
   the properties.
7. **Cal.com booking URL is an env var, not live-linked.** Set
   `CALCOM_BOOKING_URL` in `.env`; the default placeholder
   (`https://cal.com/tenacious/discovery-call`) is not a real page.
8. **τ²-Bench ran 1 trial at submission time.** Guide asks for 5; CI
   width 0.19 exceeds the 0.15 target. Re-run `python
   eval/run_baseline.py` 4 more times to tighten.

### Next steps (in inheritance order)

1. **First day**: populate `.env` from `.env.example`, run `python
   agent/check_integrations.py` (must print 4/4 PASS), then run
   `python -m playwright install chromium`.
2. **First week**: schedule a nightly cron that invokes
   `build_hiring_signal_brief` for the active prospect list, so the
   60-day velocity window starts accumulating.
3. **Second week**: wire the GitHub-org signal (item 3 above) and
   press-scrape signal (item 4). Both go into
   `_maturity_justifications` — same shape, just replace the "not
   wired" entries.
4. **Third week**: port the HubSpot property definitions into a
   migration script (`scripts/provision_hubspot_properties.py`) so a
   fresh portal can be bootstrapped without hand-clicking the UI.
5. **Ongoing**: fund alerts on OpenRouter spend and Langfuse trace
   volume — the dev-tier budget gets absorbed by a single runaway
   scrape loop if left unattended.

### Module ownership map

| Module | Owns | Don't touch without reading |
|---|---|---|
| `agent/channel_router.py` | Conversation state, HubSpot lifecycle writes, Cal.com link issuance | `_ALLOWED_TRANSITIONS` — changing it silently opens cold-SMS paths |
| `agent/enrichment.py` | Schema-compliant briefs, job-history store, silent-company acknowledgement | `_score_from_justifications` — the weighting is calibrated against 20 hand-checked CB rows |
| `agent/icp_classifier.py` | 5-rule segment precedence | `classify()` — rule order encodes policy, not preference |
| `agent/hubspot_handler.py` | MCP-first, REST-fallback contact + note writes | `_with_draft` — removing the draft stamp is a policy Rule 6 violation |
| `agent/email_handler.py` + `sms_handler.py` + `calendar_handler.py` | Provider-specific wire protocol + structured errors | Webhook payload parsing — providers reshape payloads without notice |
| `eval/run_baseline.py` | τ²-Bench harness + Langfuse hookup | The LiteLLM patches in `_patch_litellm_cost()` — removing them re-breaks cost accounting |
| `probes/run_probes.py` | 31-probe sweep + trigger-rate ledger | Probe IDs are referenced in the report; renumbering invalidates citations |

