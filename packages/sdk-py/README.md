# AgentPGO Python SDK

Metadata-only, fail-open OTLP instrumentation for Python agents. Install this package separately from the backend monorepo:

```bash
pip install ./packages/sdk-py
```

```python
from agentpgo import AgentPGOClient

client = AgentPGOClient(
    api_key="project-key",
    endpoint="https://api.agentpgo.dev/v1/traces",
    service_name="my-agent",
)

with client.trace(node="researcher", model="openai/gpt-5.6-sol", provider="openai"):
    run_agent()

client.flush_sync()
```

Prompts, outputs, credentials, and arbitrary content attributes are excluded by default. Export failures never fail the instrumented operation. In async code use `await client.flush()`; in synchronous code use `client.flush_sync()`.
