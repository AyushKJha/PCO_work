# TwineRun adapter contract tests

`tests/api/test_twinerun_adapter_contract.py` contains focused HTTP checks for
paths called by `landingpage/src/lib/api.ts` and `landingpage/src/lib/sse.ts`.
The tests use a SQLite/API-key fixture and the current queued optimization
behavior. They do not add persistence models or worker behavior.

## Current coverage

| Adapter path | Current backend result | Test status | Current compatible path |
| --- | --- | --- | --- |
| `GET /api/v1/projects` | `200`, array with `id`, `organization_id`, `name`, `slug` | passing | — |
| `GET /api/v1/projects/{projectId}` | `404` (route absent) | strict xfail | — |
| `POST /api/v1/projects/{projectId}/optimization-runs` | `404` (route absent) | strict xfail | `POST /api/v1/optimizations` returns `202` with `run_id` |
| `GET /api/v1/optimization-runs/{runId}/events` | `404` (route absent) | strict xfail | no event HTTP route yet |
| `GET /api/v1/optimization-runs/{runId}/candidates` | `404` (route absent) | strict xfail | `GET /api/v1/optimizations/{runId}/candidates` returns a list |
| `GET /api/v1/eval-runs/{runId}/cases` | `404` (route absent) | strict xfail | `GET /api/v1/evals/{datasetId}` returns persisted dataset cases |
| `GET /api/v1/projects/{projectId}/settings` | `404` (route absent) | strict xfail | no settings HTTP route yet |
| `PATCH /api/v1/projects/{projectId}/settings` | `404` (route absent) | strict xfail | no settings HTTP route yet |
| `GET /api/v1/optimization-runs/{runId}/export` | `404` (route absent) | strict xfail | `GET /api/v1/policy/export` returns verified JSON when a recommendation is persisted |

The strict xfails are intentional red contract tests: they show the missing
routes without claiming that they work. They become XPASS results when an
endpoint is implemented. `/api/v1` mirrors existing `/v1` routes only; aliasing
does not create the adapter-specific resource paths above.

The project-detail test requires a graph-compatible response containing `nodes`
and `edges`. The settings tests require browser-facing camelCase fields.
Events, candidates, and eval cases accept either a bare array or the adapter's
supported `{events|candidates|cases: [...]}` shape. Export remains
server-verified; a queued run may legitimately return `409` after the route is
added.
