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
├── agent/              Act II handlers + orchestrator
│   ├── email_handler.py   Resend outbound + reply webhook parser
│   ├── sms_handler.py     Africa's Talking SMS + STOP keywords
│   ├── hubspot_handler.py Contact upsert + engagement note
│   ├── calendar_handler.py Cal.com slot lookup + booking
│   ├── enrichment.py      Crunchbase + layoffs + jobs + AI maturity
│   ├── main_agent.py      Orchestrator: enrich → compose → write → send
│   ├── server.py          FastAPI webhook receiver
│   └── requirements.txt
├── eval/               Act I τ²-Bench reproduction
│   ├── run_baseline.py    Harness + Langfuse logger + Wilson CI
│   ├── score_log.json     Per-trial summary (pass@1, CI, cost, latency)
│   ├── trace_log.jsonl    Per-task trajectory + cost + latency + trace_id
│   └── tau2-bench/        Upstream benchmark (sierra-research/tau2-bench)
├── data/
│   ├── crunchbase-companies-information.csv
│   ├── layoffs.csv
│   └── briefs/            Generated hiring_signal_brief_*.json etc.
├── probes/             Act III (final submission)
├── baseline.md         Act I writeup (max 400 words)
└── README.md
```

## Budget

Dev-tier target (Days 1–4): < **$4** LLM spend via OpenRouter.
Week target: < **$20** total.
