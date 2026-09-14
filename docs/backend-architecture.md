# AgentPGO backend architecture

This document describes the B2B V1 control plane that powers TwineRun. The
browser talks to the versioned FastAPI API; it does not execute optimization
work and it never receives provider credentials. Customer production agents
remain independent of the optimizer: they emit metadata-only OpenTelemetry
spans and continue operating if AgentPGO is unavailable.

## Request and data flow

```mermaid
flowchart LR
    browser[TwineRun browser]
    agent[Customer agent]
    sdk[SDK or OTel exporter]
    edge[HTTPS edge\nAPI Gateway or ALB]
    api[FastAPI API\n/api/v1]
    auth[Auth + tenant scope\nAPI key or test-only demo token]
    db[(PostgreSQL\nauthoritative state)]
    s3[(S3\nlarge artifacts and exports)]
    outbox[(Transactional outbox)]
    sqs[SQS jobs + DLQ]
    worker[ECS Fargate worker]
    provider[Model providers\nOpenAI / Anthropic / Google / Backboard]
    grader[Evaluation + statistical gate]
    export[Verified recommendation\nJSON/YAML export]

    browser -->|authenticated JSON| edge
    edge --> api
    api --> auth
    auth --> db
    agent --> sdk
    sdk -->|OTLP metadata\ncontent off by default| edge
    api -->|project, trace, eval, run state| db
    api -->|large payloads| s3
    api -->|job row + outbox event in one DB tx| outbox
    outbox -->|publish job_id| sqs
    sqs --> worker
    worker -->|claim/checkpoint| db
    worker -->|candidate calls only| provider
    worker --> grader
    grader -->|evidence and gate decision| db
    db --> export
    export --> browser
```

## Optimization execution graph

Every long-running operation is durable. PostgreSQL owns the state machine and
SQS only delivers a small `job_id`; the message does not contain the full
workload. A worker can therefore be restarted and resume from the last
checkpoint without trusting process memory.

```mermaid
flowchart TD
    start[POST optimization-runs] --> validate[Validate tenant, version,\neval suite, model pool, limits]
    validate --> tx[DB transaction:\ncreate immutable run + outbox event]
    tx --> queued[QUEUED]
    queued --> publish[Outbox publisher sends job_id to SQS]
    publish --> claim[Worker claims job with lease/fence token]
    claim --> baseline[BASELINING\nrun paired baseline evidence]
    baseline --> sensitivity[SEARCHING\nsingle-node substitutions]
    sensitivity --> beam[Beam search\ncombine safe substitutions]
    beam --> halve[Successive halving\nsmall paired samples]
    halve --> verify[VERIFYING\n3-5 finalists on full suite]
    verify --> metrics[Persist cost, latency, quality,\nusage, errors, grader evidence]
    metrics --> gate{Quality tolerance +\nlatency + spend + confidence?}
    gate -->|no| reject[Candidate rejected or inconclusive]
    gate -->|yes| pareto[Pareto frontier]
    pareto --> recommendation[Persist recommendation]
    reject --> terminal[COMPLETED / PARTIAL]
    recommendation --> terminal
    terminal --> export[Server-verified export]

    claim -. worker crash .-> retry[SQS redelivery]
    retry --> claim
    claim -. stale lease token .-> fence[Reject fenced mutation]
```

## Tenant authorization boundary

The bearer token or API key is resolved before project data is read. Every
project-scoped query includes both `organization_id` and the key's optional
`project_id`. A project key cannot create projects or read another project's
traces, cases, events, candidates, layouts, or exports.

```mermaid
sequenceDiagram
    participant C as Client
    participant A as API
    participant T as Auth resolver
    participant D as PostgreSQL

    C->>A: GET /api/v1/system/overview
    A->>T: Resolve bearer/API key
    T->>D: Hash lookup, revoked_at IS NULL
    D-->>T: organization_id + optional project_id
    T-->>A: Tenant scope
    A->>D: Counts/projects WHERE organization_id AND project scope
    D-->>A: Authorized metadata only
    A-->>C: Overview + x-request-id
```

## Failure boundaries

- A provider timeout is classified and retried only within bounded worker
  policy; evaluation fallback models are not silently substituted.
- A worker crash causes SQS redelivery. Execution receipts and candidate/case
  uniqueness prevent normal duplicate provider calls; exactly-once external
  execution is not claimed where a provider lacks idempotency keys.
- A failed readiness dependency prevents traffic or work acceptance; liveness
  remains a process check.
- Prompt/output bodies are not collected by default. Content retention is an
  explicit project policy and must be redacted, encrypted, and retained for a
  bounded period.
- Browser state is never authoritative for metrics or exports. Candidates and
  recommendations are reconstructed from persisted evaluation evidence.
