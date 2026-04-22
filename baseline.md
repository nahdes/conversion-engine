# Baseline — Act I

## What was reproduced
τ²-Bench (τ³-bench v1.0.0) retail domain, 30-task dev slice, 1 trial.
Model: `deepseek/deepseek-chat` (via OpenRouter) — used for **agent**, **user
simulator**, and **NL-assertions judge**. Temperature: 0.0. Max tokens: 1024.
Full per-task trajectories are written to `eval/trace_log.jsonl`.

## Results
- pass@1: **0.0667** (2/30)
- Wilson 95% CI: **[0.019, 0.213]**
- Cost: **$0.16** for the trial
- Latency: p50 **43s**, p95 **172s**
- Completed tasks with trajectories: 23/30 (7 tasks crashed — see below)

## Confidence in reproduction
Pass@1 is below the guide's 28–38% reference band. Honest drivers:

1. **User simulator choice.** τ²-Bench reference numbers assume a GPT-4-class
   user simulator. Running DeepSeek-Chat as both agent and user sim (to stay
   in dev-tier budget) makes multi-turn tasks harder — weaker follow-ups,
   inconsistent ###STOP### signaling, occasional persona drift.
2. **Model class.** DeepSeek-Chat historically trails Qwen3-72B / GPT-4-class
   models on tool-use benchmarks. The guide's `qwen/qwen3-72b` slug does not
   exist on OpenRouter; DeepSeek-Chat was the guide's own fallback.
3. **Credit exhaustion.** Tasks 21 and 24–29 returned OpenRouter 402 errors
   ("requires more credits, or fewer max_tokens") after the account balance
   dropped. These count as failures in pass@1. On the 23 tasks that completed,
   pass rate was 2/23 = 8.7%.

CI width (0.19) exceeds the ≤0.15 target; 1 trial × 30 tasks is a small
sample. More trials would tighten the CI but not move the mean materially.

## Unexpected behavior
- **Credit exhaustion mid-run.** Task 21's NL-assertions judge requested 65K
  tokens (judge args weren't capped by our `max_tokens=1024` — that setting
  only applies to agent/user sim args). Tasks 24–29 then hit a hard 402 on
  normal 1024-token calls as the account balance drained.
- **Empty assistant messages.** DeepSeek occasionally returned messages with
  no content and no tool calls, crashing the orchestrator validator. Harness
  catches these as task failures (reward=0) rather than aborting.
- **Whitespace in tool names.** Agent emitted names like `get_user_details  `
  (trailing spaces). Harness strips whitespace at `_has_tool` and
  `make_tool_call` so lookups resolve.
- **Fenced JSON from the NL judge.** DeepSeek wrapped judge responses in
  ```json fences, breaking strict `json.loads`. Harness adds JSON-mode
  request and a tolerant parser.
- **p95 latency 172s** exceeds the guide's 120s flag, partly due to LiteLLM
  retries on the 402 errors before giving up.
- **Cost missing from LiteLLM map.** OpenRouter resolves `deepseek/deepseek-chat`
  to `deepseek/deepseek-chat-v3`. Harness registers pricing ($0.27/M input,
  $1.10/M output) at startup so `cost_usd` is populated.
