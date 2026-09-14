"""Worker process entrypoint: ``python -m services.worker.main``."""
from __future__ import annotations

import argparse
import os
from typing import Sequence

from apps.api.db import create_session_factory

from .queue import InMemoryQueue, SQSQueueConsumer
from .runner import RunnerExecutor
from .runtime import WorkerRuntime


def _unconfigured_candidate_executor(candidate: dict, _job: object) -> dict:
    """Fail closed until a real agent/provider executor is wired.

    ``WorkerRuntime`` keeps its injectable default for deterministic library
    tests and local callers.  The process entrypoint must not use that echo
    path: it would turn client-supplied cost/quality fields into a seemingly
    verified recommendation without making a provider call.
    """

    del candidate, _job
    raise RuntimeError(
        "candidate executor is not configured; refusing to publish an "
        "unverified optimization result"
    )


def _unconfigured_evaluation_executor(_case: dict, _candidate: dict, _job: dict) -> dict:
    raise RuntimeError(
        "evaluation executor is not configured; set RUNNER_ENDPOINT and "
        "RUNNER_SIGNING_SECRET before processing evaluation jobs"
    )


def build_runtime() -> WorkerRuntime:
    factory = create_session_factory(os.getenv("DATABASE_URL"))
    queue_url = os.getenv("SQS_QUEUE_URL")
    if queue_url:
        queue = SQSQueueConsumer(queue_url, dlq_url=os.getenv("SQS_DLQ_URL"))
    else:
        queue = InMemoryQueue()
    runner_endpoint = os.getenv("RUNNER_ENDPOINT")
    runner_secret = os.getenv("RUNNER_SIGNING_SECRET")
    if runner_endpoint and runner_secret:
        evaluation_executor = RunnerExecutor(
            runner_endpoint,
            signing_secret=runner_secret,
            store_content=os.getenv("AGENTPGO_STORE_CONTENT", "").lower() in {"1", "true", "yes"},
        ).execute
    else:
        evaluation_executor = _unconfigured_evaluation_executor
    return WorkerRuntime(
        factory,
        queue,
        worker_id=os.getenv("WORKER_ID"),
        candidate_executor=_unconfigured_candidate_executor,
        evaluation_executor=evaluation_executor,
        lease_seconds=int(os.getenv("WORKER_LEASE_SECONDS", "60")),
        visibility_timeout=int(os.getenv("SQS_VISIBILITY_TIMEOUT", "60")),
        max_receive_count=int(os.getenv("SQS_MAX_RECEIVE_COUNT", "3")),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the AgentPGO durable worker")
    parser.add_argument("--once", action="store_true", help="process at most one message")
    parser.add_argument("--max-iterations", type=int, default=None)
    args = parser.parse_args(argv)
    runtime = build_runtime()
    if args.once:
        runtime.process_once()
    else:
        runtime.run_forever(max_iterations=args.max_iterations)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
