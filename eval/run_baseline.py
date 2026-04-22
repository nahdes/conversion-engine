import os, json, time, statistics, datetime
from dotenv import load_dotenv
from langfuse import Langfuse

load_dotenv(override=True)
lf = Langfuse(
    secret_key=os.environ['LANGFUSE_SECRET_KEY'],
    public_key=os.environ['LANGFUSE_PUBLIC_KEY'],
    host=os.environ['LANGFUSE_HOST']
)

TRIALS = 1
DEV_TASKS = list(range(30))   # tasks 0-29 are the dev slice

# τ³-bench API: build config + preload the 30 retail tasks once.
# LiteLLM routes `openrouter/<model>` through OpenRouter using OPENROUTER_API_KEY.
from tau2.data_model.simulation import TextRunConfig
from tau2.runner.batch import run_single_task
from tau2.runner.helpers import get_tasks

_model = os.environ['TAU2_MODEL']
if '/' in _model and not _model.startswith('openrouter/'):
    _model = f'openrouter/{_model}'

# tau2's NL-assertions judge and env-interface LLM are read from module-level
# constants, not TextRunConfig. Override both so they hit OpenRouter too.
import tau2.config as _tau2_config
import tau2.evaluator.evaluator_nl_assertions as _tau2_nl
_tau2_config.DEFAULT_LLM_NL_ASSERTIONS = _model
_tau2_config.DEFAULT_LLM_ENV_INTERFACE = _model
_tau2_nl.DEFAULT_LLM_NL_ASSERTIONS = _model

# Ask DeepSeek for JSON mode in the NL-judge request.
_tau2_config.DEFAULT_LLM_NL_ASSERTIONS_ARGS = {
    'temperature': 0.0,
    'response_format': {'type': 'json_object'},
}
_tau2_nl.DEFAULT_LLM_NL_ASSERTIONS_ARGS = (
    _tau2_config.DEFAULT_LLM_NL_ASSERTIONS_ARGS
)

# DeepSeek still sometimes wraps JSON in ```json fences. Make the evaluator's
# json.loads tolerant of that.
import json as _stdlib_json
import re as _re

class _TolerantJson:
    def loads(self, s, *args, **kwargs):
        try:
            return _stdlib_json.loads(s, *args, **kwargs)
        except _stdlib_json.JSONDecodeError:
            text = s.strip()
            if text.startswith('```'):
                text = text.split('\n', 1)[-1]
                if text.rstrip().endswith('```'):
                    text = text.rsplit('```', 1)[0]
                text = text.strip()
            try:
                return _stdlib_json.loads(text, *args, **kwargs)
            except _stdlib_json.JSONDecodeError:
                m = _re.search(r'(\{.*\}|\[.*\])', text, _re.DOTALL)
                if m:
                    return _stdlib_json.loads(m.group(1), *args, **kwargs)
                raise

    def __getattr__(self, name):
        return getattr(_stdlib_json, name)

_tau2_nl.json = _TolerantJson()

# DeepSeek sometimes emits tool names with trailing whitespace ("get_user_details  ").
# Strip it at the environment's lookup boundaries so replays don't fail.
import tau2.environment.environment as _env_mod
_orig_has_tool = _env_mod.Environment._has_tool
_orig_make_tool_call = _env_mod.Environment.make_tool_call

def _patched_has_tool(self, tool_name):
    if isinstance(tool_name, str):
        tool_name = tool_name.strip()
    return _orig_has_tool(self, tool_name)

def _patched_make_tool_call(self, tool_name, *args, **kwargs):
    if isinstance(tool_name, str):
        tool_name = tool_name.strip()
    return _orig_make_tool_call(self, tool_name, *args, **kwargs)

_env_mod.Environment._has_tool = _patched_has_tool
_env_mod.Environment.make_tool_call = _patched_make_tool_call

# Register DeepSeek Chat v3 pricing with LiteLLM so sim.agent_cost is populated.
# OpenRouter resolves `deepseek/deepseek-chat` to `deepseek/deepseek-chat-v3`.
# Prices: see openrouter.ai/models/deepseek/deepseek-chat — update if they change.
import litellm
litellm.register_model({
    'openrouter/deepseek/deepseek-chat': {
        'input_cost_per_token': 0.27e-6,
        'output_cost_per_token': 1.10e-6,
        'litellm_provider': 'openrouter',
        'mode': 'chat',
    },
    'deepseek/deepseek-chat-v3': {
        'input_cost_per_token': 0.27e-6,
        'output_cost_per_token': 1.10e-6,
        'litellm_provider': 'openrouter',
        'mode': 'chat',
    },
})

_llm_args = {
    'temperature': float(os.environ['TAU2_TEMPERATURE']),
    'max_tokens': int(os.environ['TAU2_MAX_TOKENS']),
}
TAU2_CONFIG = TextRunConfig(
    domain='retail',
    agent='llm_agent',
    llm_agent=_model,
    llm_args_agent=dict(_llm_args),
    llm_user=_model,
    llm_args_user=dict(_llm_args),
)
TAU2_TASKS = get_tasks('retail', num_tasks=max(DEV_TASKS) + 1)

def run_tau2_task(task_id: int) -> dict:
    """Run one τ²-Bench task and return result dict."""
    task = TAU2_TASKS[task_id]
    start = time.time()
    try:
        sim = run_single_task(TAU2_CONFIG, task, seed=task_id)
        latency = time.time() - start
        reward = sim.reward_info.reward if sim.reward_info else 0.0
        trajectory = [
            m.model_dump(mode='json', exclude_none=True)
            for m in (sim.messages or [])
        ]
        return {
            'task_id': task_id,
            'passed': reward >= 1.0,
            'reward': reward,
            'cost_usd': sim.agent_cost or 0.0,
            'latency_s': latency,
            'error': None,
            'trajectory': trajectory,
        }
    except Exception as e:
        latency = time.time() - start
        return {
            'task_id': task_id,
            'passed': False,
            'reward': 0.0,
            'cost_usd': 0.0,
            'latency_s': latency,
            'error': f'{type(e).__name__}: {e}',
        }

def wilson_ci(successes, n, z=1.96):
    """Wilson score 95% CI for pass@1."""
    p = successes / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2*n)) / denom
    margin = (z * (p*(1-p)/n + z**2/(4*n**2))**0.5) / denom
    return round(centre - margin, 4), round(centre + margin, 4)

all_traces = []
score_log = []

for trial in range(TRIALS):
    passes, costs, latencies = 0, [], []
    trial_traces = []

    for task_id in DEV_TASKS:
        with lf.start_as_current_observation(
            name=f'tau2_t{trial}_task{task_id}', as_type='span'
        ) as span:
            result = run_tau2_task(task_id)
            result['trial'] = trial
            result['trace_id'] = lf.get_current_trace_id()

            span.update(
                input={'task_id': task_id, 'trial': trial},
                output={'passed': result['passed']},
                metadata={'cost_usd': result['cost_usd'],
                          'latency_s': result['latency_s']}
            )

        passes += int(result['passed'])
        costs.append(result['cost_usd'])
        latencies.append(result['latency_s'])
        trial_traces.append(result)

    pass_at_1 = passes / len(DEV_TASKS)
    lo, hi = wilson_ci(passes, len(DEV_TASKS))
    entry = {
        'trial': trial,
        'pass_at_1': round(pass_at_1, 4),
        'ci_95': [lo, hi],
        'cost_usd': round(sum(costs), 4),
        'p50_latency_s': round(statistics.median(latencies), 2),
        'p95_latency_s': round(sorted(latencies)[int(0.95*len(latencies))], 2),
        'timestamp': datetime.datetime.utcnow().isoformat()
    }
    score_log.append(entry)
    all_traces.extend(trial_traces)
    print(f'Trial {trial}: pass@1={pass_at_1:.2%}  CI=[{lo:.3f}, {hi:.3f}]')

# Write outputs
with open('eval/score_log.json', 'w') as f:
    json.dump(score_log, f, indent=2)

with open('eval/trace_log.jsonl', 'w') as f:
    for t in all_traces:
        f.write(json.dumps(t) + '\n')

lf.flush()
print('Done. Files written to eval/')