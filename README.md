# AgentPGO

Profile-guided optimization for AI agents.

## Backend V1

This worktree contains the backend-first B2B V1. The unrelated `landingpage/` is preserved unchanged.

- FastAPI API and OTLP ingestion: `apps/api/`
- TypeScript SDK and Vercel AI SDK adapter: `packages/`
- Profiling, evaluation, optimization, statistical gates, and provider seams: `services/`
- CLI: `cli/`
- Alembic schema: `migrations/`

## Local verification
http://127.0.0.1:3000/
```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest -q
node --experimental-strip-types --test packages/*/tests/*.test.ts
```

## Run API

```bash
uvicorn apps.api.main:app --reload
```

The API accepts OTLP JSON at `/v1/traces` and `/v1/otlp/v1/traces`. All protected endpoints require a tenant-scoped API key.

## Connector layer

Any agent can connect by emitting OTLP/HTTP JSON to either ingestion endpoint. Use the TypeScript package (`@agentpgo/sdk`) or install the standalone Python package from `packages/sdk-py` (`agentpgo-sdk`) for in-process tracing:

```python
from agentpgo import AgentPGOClient

client = AgentPGOClient(
    api_key="project-key",
    project_id="project-id",  # required for organization-scoped keys
    endpoint="https://api.agentpgo.dev/v1/traces",
    service_name="my-agent",
)

with client.trace(node="researcher", model="openai/gpt-5.6-sol", provider="openai"):
    run_agent()

client.flush_sync()
```

Instrumentation is metadata-only by default (model, node, provider, tokens, latency, status, and tool-call count). Prompt/output content is not collected by these connectors. Export is fail-open, so a telemetry outage does not interrupt the agent.

## Scope boundaries

V1 produces recommendations and YAML exports only. It does not change production routing, collect prompt/output content by default, implement a frontend, or integrate payments.

## Open Deep Research benchmark

The bounded benchmark adapter lives in `scripts/run_odr_benchmark.py`. It uses the fork at `/home/lenovo/Documents/open_deep_research` by default.

Replay historical reports (no network or provider calls):

```bash
.venv/bin/python scripts/run_odr_benchmark.py --mode replay --tasks 20 --search-tasks 50
```

For an explicit Backboard live smoke, place `BACKBOARD_API_KEY`, optional `BACKBOARD_BASE_URL`, and optional `BACKBOARD_LLM_PROVIDER` in the Open Deep Research fork's `.env`, then run only a bounded task first:

```bash
.venv/bin/python scripts/run_odr_benchmark.py --mode backboard --tasks 1 --search-tasks 1 --model-pool backboard:gpt-luna-5.6
```

The replay metric is a historical-report overlap proxy, not Deep Research Bench RACE. A live run is only a real provider benchmark when the output records `mode: backboard` and completes with provider usage/latency evidence.
