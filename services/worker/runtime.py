"""Durable SQS job execution runtime.

The runtime keeps provider work behind an injected callable and persists every
state transition before acknowledging a queue message.  It is therefore safe
to restart a worker after a lease expiry: PostgreSQL claims are serialized
with ``FOR UPDATE SKIP LOCKED`` and candidate rows are unique per job.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from enum import Enum
import inspect
import math
import os
from threading import Event, Lock, Thread
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from apps.api.models import (
    EvalCase, EvalDataset, EvalRun, EvalRunCase,
    Job, JobCandidateResult, OptimizationResult, utc_now,
)

from .queue import QueueConsumer, QueueMessage


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    EVALUATING = "evaluating"
    VALIDATING = "validating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = {JobState.COMPLETED.value, JobState.FAILED.value, JobState.CANCELLED.value}


def _utc(value: Any) -> datetime | None:
    """Normalize SQLite's naive timestamps to UTC-aware values."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class SpendLimitExceeded(RuntimeError):
    pass


class LeaseLostError(RuntimeError):
    """Raised when a worker attempts a mutation after losing its lease."""

    pass


class JobRepository:
    """Small persistence facade; all mutations commit in short transactions."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def create(self, **kwargs: Any) -> Job:
        with self.session_factory.begin() as session:
            job = Job(**kwargs)
            session.add(job)
            session.flush()
            session.refresh(job)
            return job

    def get(self, job_id: str) -> Job | None:
        with self.session_factory() as session:
            return session.scalar(
                select(Job).options(selectinload(Job.candidate_results)).where(Job.id == job_id)
            )

    def cancellation_requested(self, job_id: str) -> bool:
        with self.session_factory() as session:
            job = session.get(Job, job_id)
            return bool(job and job.cancel_requested_at is not None)

    def append_event(self, job_id: str, event_type: str, payload: dict[str, Any], *, event_id: str | None = None) -> dict[str, Any] | None:
        """Append an optimization event with a monotonic per-run sequence."""
        from apps.api.models import OptimizationEvent
        with self.session_factory.begin() as session:
            job = session.get(Job, job_id, with_for_update=True)
            if job is None or job.kind != "optimization":
                return None
            stable_id = event_id or f"{job_id}:{uuid4().hex}"
            existing = session.scalar(select(OptimizationEvent).where(OptimizationEvent.job_id == job_id, OptimizationEvent.event_id == stable_id))
            if existing is not None:
                return {"id": existing.event_id, "sequence": existing.sequence, "type": existing.event_type, **(existing.payload or {})}
            last = session.scalar(select(func.max(OptimizationEvent.sequence)).where(OptimizationEvent.job_id == job_id))
            event = OptimizationEvent(
                organization_id=job.organization_id, project_id=job.project_id, job_id=job_id,
                sequence=int(last or 0) + 1, event_id=stable_id, event_type=event_type.upper(), payload=dict(payload),
            )
            session.add(event)
            session.flush()
            return {"id": event.event_id, "sequence": event.sequence, "type": event.event_type, "timestamp": event.created_at.isoformat(), **payload}

    def claim_next(self, worker_id: str, *, lease_seconds: int = 60) -> Job | None:
        """Atomically claim one available queued/expired job.

        PostgreSQL uses row locks with ``SKIP LOCKED`` so competing workers do
        not block one another.  SQLite ignores that clause and still provides
        the same transactionally atomic update for local tests.
        """
        lease_seconds = max(1, int(lease_seconds))
        now = utc_now()
        with self.session_factory.begin() as session:
            query = (
                select(Job)
                .options(selectinload(Job.candidate_results))
                .where(
                    Job.status == JobState.QUEUED.value,
                    Job.available_at <= now,
                )
                .order_by(Job.created_at, Job.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            job = session.scalar(query)
            if job is None:
                # A worker may have died while holding a lease.  Reclaim only
                # running rows whose lease is actually expired.
                query = (
                    select(Job)
                    .options(selectinload(Job.candidate_results))
                    .where(
                        Job.status.in_((JobState.RUNNING.value, JobState.EVALUATING.value, JobState.VALIDATING.value)),
                        Job.claim_expires_at.is_not(None),
                        Job.claim_expires_at < now,
                        Job.available_at <= now,
                    )
                    .order_by(Job.created_at, Job.id)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
                job = session.scalar(query)
            if job is None:
                return None
            reclaim = job.status in {JobState.RUNNING.value, JobState.EVALUATING.value, JobState.VALIDATING.value}
            self._claim(job, worker_id, now, lease_seconds, reclaim=reclaim)
            session.flush()
            return job

    def claim(self, job_id: str, worker_id: str, *, lease_seconds: int = 60) -> Job | None:
        lease_seconds = max(1, int(lease_seconds))
        now = utc_now()
        with self.session_factory.begin() as session:
            job = session.scalar(
                select(Job)
                .options(selectinload(Job.candidate_results))
                .where(Job.id == job_id)
                .with_for_update(skip_locked=True)
            )
            if job is None or job.status in TERMINAL_STATES:
                return job
            reclaim = job.status in {JobState.RUNNING.value, JobState.EVALUATING.value, JobState.VALIDATING.value}
            if reclaim:
                if job.claim_expires_at is None or _utc(job.claim_expires_at) > now:
                    return None
            elif job.status != JobState.QUEUED.value:
                return None
            self._claim(job, worker_id, now, lease_seconds, reclaim=reclaim)
            session.flush()
            return job

    @staticmethod
    def _claim(job: Job, worker_id: str, now: Any, lease_seconds: int, *, reclaim: bool = False) -> None:
        if reclaim:
            checkpoint = dict(job.checkpoint or {})
            reservations = dict(checkpoint.pop("spend_reservations", {}) or {})
            if reservations:
                job.spent_usd = max(0.0, float(job.spent_usd or 0.0) - sum(float(value) for value in reservations.values()))
                job.checkpoint = checkpoint
        job.status = JobState.RUNNING.value
        job.claimed_by = worker_id
        job.claim_expires_at = now + timedelta(seconds=lease_seconds)
        job.attempt_count = int(job.attempt_count or 0) + 1
        job.started_at = job.started_at or now
        job.error = None

    @staticmethod
    def _lease_matches(job: Job, worker_id: str, lease_token: Any | None) -> bool:
        return (
            job.claimed_by == worker_id
            and job.claim_expires_at is not None
            and _utc(job.claim_expires_at) > _utc(utc_now())
            and (lease_token is None or _utc(job.claim_expires_at) == _utc(lease_token))
        )

    def transition(self, job_id: str, state: JobState | str, *, worker_id: str | None = None, lease_token: Any | None = None, error: str | None = None) -> Job | None:
        state_value = state.value if isinstance(state, JobState) else str(state)
        allowed = {s.value for s in JobState}
        if state_value not in allowed:
            raise ValueError(f"unsupported job state: {state_value}")
        with self.session_factory.begin() as session:
            job = session.get(Job, job_id, with_for_update=True)
            if job is None:
                return None
            if state_value in TERMINAL_STATES and worker_id is None:
                return None
            if worker_id is None and job.claimed_by is not None:
                return None
            if worker_id is not None and not self._lease_matches(job, worker_id, lease_token):
                return None
            job.status = state_value
            job.error = error
            if state_value in TERMINAL_STATES:
                job.completed_at = utc_now()
                job.claim_expires_at = None
                job.claimed_by = None
            session.flush()
            return job

    def checkpoint(self, job_id: str, checkpoint: dict[str, Any], *, spent_usd: float | None = None, worker_id: str | None = None, lease_token: Any | None = None) -> Job | None:
        with self.session_factory.begin() as session:
            job = session.get(Job, job_id, with_for_update=True)
            if job is None:
                return None
            if worker_id and (job.claimed_by != worker_id or (lease_token is not None and _utc(job.claim_expires_at) != _utc(lease_token)) or (job.claim_expires_at is not None and _utc(job.claim_expires_at) <= utc_now())):
                return None
            job.checkpoint = dict(checkpoint)
            if spent_usd is not None:
                job.spent_usd = float(spent_usd)
            session.flush()
            return job

    def renew_lease(self, job_id: str, worker_id: str, *, lease_seconds: int = 60, lease_token: Any | None = None) -> Job | None:
        lease_seconds = max(1, int(lease_seconds))
        now = utc_now()
        with self.session_factory.begin() as session:
            job = session.get(Job, job_id, with_for_update=True)
            if job is None or job.status in TERMINAL_STATES or job.claimed_by != worker_id:
                return None
            if job.claim_expires_at is None or _utc(job.claim_expires_at) <= now or (lease_token is not None and _utc(job.claim_expires_at) != _utc(lease_token)):
                return None
            job.claim_expires_at = now + timedelta(seconds=lease_seconds)
            session.flush()
            return job

    def reserve_spend(self, job_id: str, candidate_id: str, amount_usd: float, *, worker_id: str | None = None, lease_token: Any | None = None) -> bool:
        amount = float(amount_usd)
        if amount < 0:
            raise ValueError("reservation amount cannot be negative")
        with self.session_factory.begin() as session:
            job = session.get(Job, job_id, with_for_update=True)
            if job is None:
                return False
            if worker_id and (job.claimed_by != worker_id or (lease_token is not None and _utc(job.claim_expires_at) != _utc(lease_token)) or (job.claim_expires_at is not None and _utc(job.claim_expires_at) <= utc_now())):
                return False
            checkpoint = dict(job.checkpoint or {})
            reservations = dict(checkpoint.get("spend_reservations", {}))
            if candidate_id in reservations:
                return False
            cap = float(job.max_experiment_cost_usd or 0.0)
            if float(job.spent_usd or 0.0) + amount > cap + 1e-12:
                return False
            reservations[str(candidate_id)] = amount
            checkpoint["spend_reservations"] = reservations
            job.checkpoint = checkpoint
            job.spent_usd = float(job.spent_usd or 0.0) + amount
            session.flush()
            return True

    def release_spend_reservation(self, job_id: str, candidate_id: str, *, worker_id: str | None = None, lease_token: Any | None = None) -> bool:
        with self.session_factory.begin() as session:
            job = session.get(Job, job_id, with_for_update=True)
            if job is None:
                return False
            if worker_id and (job.claimed_by != worker_id or (lease_token is not None and _utc(job.claim_expires_at) != _utc(lease_token))):
                return False
            checkpoint = dict(job.checkpoint or {})
            reservations = dict(checkpoint.get("spend_reservations", {}))
            amount = reservations.pop(str(candidate_id), None)
            if amount is None:
                return False
            checkpoint["spend_reservations"] = reservations
            job.checkpoint = checkpoint
            job.spent_usd = max(0.0, float(job.spent_usd or 0.0) - float(amount))
            session.flush()
            return True

    def record_candidate(
        self, job_id: str, candidate_id: str, result: dict[str, Any], cost_usd: float, checkpoint: dict[str, Any], *, worker_id: str | None = None, lease_token: Any | None = None, reserved_usd: float | None = None
    ) -> tuple[JobCandidateResult, bool]:
        """Insert a candidate once and atomically checkpoint its spend.

        Returns ``(row, inserted)``.  A duplicate candidate is never charged a
        second time, even when a worker receives the same SQS message twice.
        """
        with self.session_factory.begin() as session:
            job = session.get(Job, job_id, with_for_update=True)
            if job is None:
                raise KeyError(job_id)
            if worker_id and (job.claimed_by != worker_id or (lease_token is not None and _utc(job.claim_expires_at) != _utc(lease_token)) or (job.claim_expires_at is not None and _utc(job.claim_expires_at) <= utc_now())):
                raise LeaseLostError(job_id)
            existing = session.scalar(
                select(JobCandidateResult).where(
                    JobCandidateResult.job_id == job_id,
                    JobCandidateResult.candidate_id == candidate_id,
                )
            )
            if existing is not None:
                return existing, False
            row = JobCandidateResult(
                job_id=job_id,
                candidate_id=candidate_id,
                status=JobState.COMPLETED.value,
                result=dict(result),
                cost_usd=float(cost_usd),
            )
            session.add(row)
            next_checkpoint = dict(checkpoint)
            reservations = dict(next_checkpoint.get("spend_reservations", {}))
            reserved = reservations.pop(str(candidate_id), None) if reserved_usd is not None else None
            next_checkpoint["spend_reservations"] = reservations
            job.checkpoint = next_checkpoint
            if reserved_usd is None:
                job.spent_usd = float(job.spent_usd or 0) + float(cost_usd)
            else:
                job.spent_usd = float(job.spent_usd or 0) + float(cost_usd) - float(reserved if reserved is not None else reserved_usd)
            session.flush()
            return row, True

    def set_result(self, job_id: str, result: dict[str, Any], *, worker_id: str | None = None, lease_token: Any | None = None) -> Job | None:
        with self.session_factory.begin() as session:
            job = session.get(Job, job_id, with_for_update=True)
            if job is None:
                return None
            if worker_id and (job.claimed_by != worker_id or (lease_token is not None and _utc(job.claim_expires_at) != _utc(lease_token)) or (job.claim_expires_at is not None and _utc(job.claim_expires_at) <= utc_now())):
                return None
            job.result = dict(result)
            session.flush()
            return job

    def release_for_retry(
        self, job_id: str, error: str, *, delay_seconds: int = 0, worker_id: str | None = None, lease_token: Any | None = None
    ) -> bool:
        with self.session_factory.begin() as session:
            job = session.get(Job, job_id, with_for_update=True)
            if job is None or job.status in TERMINAL_STATES:
                return False
            if worker_id is None or not self._lease_matches(job, worker_id, lease_token):
                return False
            job.status = JobState.QUEUED.value
            job.available_at = utc_now() + timedelta(seconds=max(0, int(delay_seconds)))
            job.claimed_by = None
            job.claim_expires_at = None
            job.error = error[:4000]
            session.flush()
            return True

    def persist_optimization_result(
        self, job_id: str, recommendation: dict[str, Any], *, status: str = JobState.COMPLETED.value,
        metadata: dict[str, Any] | None = None, worker_id: str | None = None, lease_token: Any | None = None,
    ) -> OptimizationResult | None:
        with self.session_factory.begin() as session:
            job = session.get(Job, job_id, with_for_update=True)
            if job is None or job.kind != "optimization" or worker_id is None or not self._lease_matches(job, worker_id, lease_token):
                return None
            value = dict(recommendation)
            current = dict(job.result or {})
            current["recommendation"] = value
            job.result = current
            result = session.scalar(select(OptimizationResult).where(OptimizationResult.job_id == job_id).with_for_update())
            if result is None:
                result = OptimizationResult(organization_id=job.organization_id, project_id=job.project_id, job_id=job.id, status=status, recommendation=value, metadata_json=dict(metadata or {}))
                session.add(result)
            else:
                result.status = status
                result.recommendation = value
                if metadata is not None:
                    result.metadata_json = dict(metadata)
            session.flush()
            return result



    def evaluation_context(self, job_id: str, *, worker_id: str, lease_token: Any | None) -> dict[str, Any]:
        """Return a detached snapshot of the immutable suite for one run."""
        with self.session_factory.begin() as session:
            job = session.get(Job, job_id, with_for_update=True)
            if job is None or job.kind != "evaluation" or not self._lease_matches(job, worker_id, lease_token):
                raise LeaseLostError(job_id)
            payload = job.payload if isinstance(job.payload, dict) else {}
            run_id = str(payload.get("eval_run_id") or job.id)
            run = session.get(EvalRun, run_id, with_for_update=True)
            if run is None:
                raise ValueError(f"evaluation run not found: {run_id}")
            dataset_id = str(run.eval_suite_id or payload.get("eval_suite_id") or job.dataset_id or "")
            dataset = session.get(EvalDataset, dataset_id)
            if dataset is None:
                raise ValueError(f"evaluation dataset not found: {dataset_id}")
            dataset_cases = {case.case_id: case for case in session.scalars(select(EvalCase).where(EvalCase.dataset_id == dataset.id)).all()}
            rows = list(session.scalars(select(EvalRunCase).where(EvalRunCase.eval_run_id == run.id).order_by(EvalRunCase.ordinal, EvalRunCase.id)).all())
            if not rows:
                rows = [EvalRunCase(eval_run_id=run.id, case_id=case.case_id, ordinal=case.ordinal) for case in dataset.cases]
                session.add_all(rows)
                session.flush()
            if run.status not in {"completed", "failed", "cancelled"}:
                run.status = "running"
                run.started_at = run.started_at or utc_now()
                run.error = None
            cases = []
            for row in rows:
                source = dataset_cases.get(row.case_id)
                if source is None:
                    raise ValueError(f"evaluation case not found: {row.case_id}")
                cases.append({"case_id": row.case_id, "input": source.input_data, "expected": source.expected, "metadata": source.metadata_json or {}, "ordinal": row.ordinal, "status": row.status})
            session.flush()
            return {"run_id": run.id, "organization_id": run.organization_id, "project_id": run.project_id, "candidate": dict(run.candidate_config or {}), "cases": cases}

    def mark_evaluation_case_running(self, job_id: str, run_id: str, case_id: str, execution_key: str, *, worker_id: str, lease_token: Any | None) -> EvalRunCase | None:
        """Durably claim a provider execution before dispatching it.

        The execution key is stable for a run/case pair so a customer runner
        can deduplicate retries after a worker crash.
        """
        with self.session_factory.begin() as session:
            job = session.get(Job, job_id, with_for_update=True)
            if job is None or job.kind != "evaluation" or not self._lease_matches(job, worker_id, lease_token):
                return None
            run = session.get(EvalRun, run_id, with_for_update=True)
            if run is None:
                return None
            row = session.scalar(select(EvalRunCase).where(EvalRunCase.eval_run_id == run_id, EvalRunCase.case_id == case_id).with_for_update())
            if row is None:
                row = EvalRunCase(eval_run_id=run_id, case_id=case_id)
                session.add(row)
            evidence = dict(row.evidence or {})
            evidence.update({"execution_key": execution_key, "attempted_at": utc_now().isoformat()})
            row.status = "running"
            row.evidence = evidence
            session.flush()
            return row

    def persist_evaluation_case(self, job_id: str, run_id: str, case_id: str, *, status: str, score: float | None = None, passed: bool | None = None, latency_ms: float | None = None, evidence: dict[str, Any] | None = None, worker_id: str, lease_token: Any | None) -> EvalRunCase | None:
        with self.session_factory.begin() as session:
            job = session.get(Job, job_id, with_for_update=True)
            if job is None or job.kind != "evaluation" or not self._lease_matches(job, worker_id, lease_token):
                return None
            run = session.get(EvalRun, run_id, with_for_update=True)
            if run is None:
                return None
            row = session.scalar(select(EvalRunCase).where(EvalRunCase.eval_run_id == run_id, EvalRunCase.case_id == case_id).with_for_update())
            if row is None:
                row = EvalRunCase(eval_run_id=run_id, case_id=case_id)
                session.add(row)
            row.status = str(status)
            row.score = score
            row.passed = passed
            row.latency_ms = latency_ms
            row.evidence = dict(evidence or {})
            session.flush()
            return row

    def finish_evaluation(self, job_id: str, run_id: str, *, status: str, aggregate_metrics: dict[str, Any] | None = None, error: str | None = None, worker_id: str, lease_token: Any | None) -> EvalRun | None:
        with self.session_factory.begin() as session:
            job = session.get(Job, job_id, with_for_update=True)
            if job is None or job.kind != "evaluation" or not self._lease_matches(job, worker_id, lease_token):
                return None
            run = session.get(EvalRun, run_id, with_for_update=True)
            if run is None:
                return None
            run.status = str(status)
            run.error = error[:4000] if error else None
            if aggregate_metrics is not None:
                run.aggregate_metrics = dict(aggregate_metrics)
            if run.status in {"completed", "failed", "cancelled"}:
                run.completed_at = utc_now()
            session.flush()
            return run


CandidateExecutor = Callable[..., dict[str, Any] | float | int]
EvaluationExecutor = Callable[..., Any]


class WorkerRuntime:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        queue: QueueConsumer,
        *,
        worker_id: str | None = None,
        candidate_executor: CandidateExecutor | None = None,
        execute_candidate: CandidateExecutor | None = None,
        evaluation_executor: EvaluationExecutor | None = None,
        lease_seconds: int = 60,
        visibility_timeout: int = 60,
        max_receive_count: int = 3,
        retry_delay_seconds: int = 1,
        allow_legacy_payload: bool = False,
        lease_renewal_hook: Callable[[Job], None] | None = None,
    ) -> None:
        self.repository = JobRepository(session_factory)
        self.queue = queue
        self.worker_id = worker_id or f"worker-{uuid4()}"
        self.candidate_executor = candidate_executor or execute_candidate or self._default_candidate_executor
        self.evaluation_executor = evaluation_executor or self._default_evaluation_executor
        self.lease_seconds = max(1, int(lease_seconds))
        self.visibility_timeout = max(0, min(int(visibility_timeout), 43_200))
        self.max_receive_count = max(1, int(max_receive_count))
        self.retry_delay_seconds = max(0, int(retry_delay_seconds))
        self.allow_legacy_payload = bool(allow_legacy_payload)
        self.lease_renewal_hook = lease_renewal_hook
        self._lease_token: Any | None = None
        self._active_message: QueueMessage | None = None
        self._lease_lock = Lock()
        self._executor_lease_lost: Exception | None = None

    @staticmethod
    def _default_candidate_executor(candidate: dict[str, Any], _job: Job) -> dict[str, Any]:
        return dict(candidate)

    @staticmethod
    def _default_evaluation_executor(_case: dict[str, Any], _candidate: dict[str, Any], _job: dict[str, Any]) -> Any:
        raise RuntimeError(
            "evaluation executor is not configured; refusing to publish an "
            "unverified evaluation result"
        )

    @staticmethod
    def _store_content() -> bool:
        return os.getenv("AGENTPGO_STORE_CONTENT", "").lower() in {"1", "true", "yes"}

    @staticmethod
    def _quality_samples(value: Any) -> tuple[float, ...] | None:
        """Return paired quality samples when a result explicitly provides them."""

        if not isinstance(value, dict) or "quality_samples" not in value:
            return None
        samples = value["quality_samples"]
        if not isinstance(samples, (list, tuple)):
            raise ValueError("quality_samples must be a list")
        return tuple(float(sample) for sample in samples)

    @staticmethod
    def _optimization_config_value(config: dict[str, Any], gate_config: dict[str, Any], key: str, default: Any) -> Any:
        return gate_config.get(key, config.get(key, default))

    def process_once(self) -> bool:
        messages = self.queue.receive(max_messages=1, visibility_timeout=self.visibility_timeout)
        if not messages:
            return False
        message = messages[0]
        job_id = message.body.get("job_id") if isinstance(message.body, dict) else None
        if not isinstance(job_id, str) or not job_id:
            self.queue.move_to_dlq(message)
            return False
        existing = self.repository.get(job_id)
        if existing is None:
            self.queue.move_to_dlq(message)
            return False
        if existing.status in TERMINAL_STATES:
            self.queue.acknowledge(message)
            return False
        if message.receive_count > self.max_receive_count:
            # Claim before failing so a late/duplicate message cannot terminally
            # overwrite another worker's active lease.
            claimed = self.repository.claim(job_id, self.worker_id, lease_seconds=self.lease_seconds)
            if claimed is not None:
                self.repository.transition(job_id, JobState.FAILED, worker_id=self.worker_id, lease_token=claimed.claim_expires_at, error="message retry limit exceeded")
            self.queue.move_to_dlq(message)
            return False
        job = self.repository.claim(job_id, self.worker_id, lease_seconds=self.lease_seconds)
        if job is None:
            self.queue.retry(message, visibility_timeout=self.visibility_timeout)
            return False
        self._lease_token = job.claim_expires_at
        self._active_message = message
        try:
            self._execute(job)
        except SpendLimitExceeded as exc:
            if job.kind == "evaluation":
                self._fail_evaluation_run(job, str(exc))
            self.repository.transition(job.id, JobState.FAILED, worker_id=self.worker_id, lease_token=self._lease_token, error=str(exc))
        except Exception as exc:
            if message.receive_count >= self.max_receive_count or job.attempt_count >= job.max_attempts:
                if job.kind == "evaluation":
                    self._fail_evaluation_run(job, str(exc))
                self.repository.transition(job.id, JobState.FAILED, worker_id=self.worker_id, lease_token=self._lease_token, error=str(exc))
                self.queue.move_to_dlq(message)
            else:
                self.repository.release_for_retry(job.id, str(exc), delay_seconds=self.retry_delay_seconds, worker_id=self.worker_id, lease_token=self._lease_token)
                self.queue.retry(message, visibility_timeout=self.visibility_timeout)
            return True
        self.queue.acknowledge(message)
        self._active_message = None
        self._lease_token = None
        return True

    def _fail_evaluation_run(self, job: Job, error: str) -> None:
        payload = job.payload if isinstance(job.payload, dict) else {}
        run_id = str(payload.get("eval_run_id") or job.id)
        self.repository.finish_evaluation(job.id, run_id, status="failed", error=error, worker_id=self.worker_id, lease_token=self._lease_token)

    def _extend_visibility(self, message: QueueMessage) -> None:
        extender = getattr(self.queue, "extend_visibility", None)
        if extender is not None:
            extender(message, self.visibility_timeout)

    def _renew_lease(self, job: Job, message: QueueMessage | None = None) -> None:
        with self._lease_lock:
            token = self._lease_token
            renewed = self.repository.renew_lease(job.id, self.worker_id, lease_seconds=self.lease_seconds, lease_token=token)
            if renewed is None:
                raise LeaseLostError(job.id)
            self._lease_token = renewed.claim_expires_at
        if self.lease_renewal_hook:
            self.lease_renewal_hook(renewed)
        if message is not None:
            self._extend_visibility(message)

    def _invoke_with_heartbeat(self, candidate: dict[str, Any], job: Job) -> dict[str, Any] | float | int:
        stop = Event()
        interval = max(0.05, min(self.lease_seconds / 3.0, 5.0))
        self._executor_lease_lost = None

        def heartbeat() -> None:
            while not stop.wait(interval):
                try:
                    self._renew_lease(job, self._active_message)
                except Exception as exc:  # the executor may still be in flight
                    self._executor_lease_lost = exc
                    stop.set()
                    return

        thread = Thread(target=heartbeat, name="agentpgo-lease-heartbeat", daemon=True)
        thread.start()
        try:
            result = self._invoke_executor(candidate, job)
        finally:
            stop.set()
            thread.join(timeout=max(1.0, interval * 2))
        if self._executor_lease_lost is not None:
            raise LeaseLostError(job.id) from self._executor_lease_lost
        return result

    def _cancel_if_requested(self, job: Job, *, stage: str) -> bool:
        if job.kind != "optimization" or not self.repository.cancellation_requested(job.id):
            return False
        self.repository.append_event(job.id, "INFO", {"message": "Optimization cancelled", "status": "CANCELLED", "stage": stage}, event_id=f"{job.id}:cancelled")
        if self.repository.transition(job.id, JobState.CANCELLED, worker_id=self.worker_id, lease_token=self._lease_token) is None:
            raise LeaseLostError(job.id)
        return True

    def _execute(self, claimed: Job) -> None:
        job = self.repository.get(claimed.id)
        if job is None:
            raise KeyError(claimed.id)
        if self._cancel_if_requested(job, stage="start"):
            return
        payload = job.payload if isinstance(job.payload, dict) else {}
        if job.kind == "profile":
            if self.repository.transition(job.id, JobState.EVALUATING, worker_id=self.worker_id, lease_token=self._lease_token) is None:
                raise LeaseLostError(job.id)
            profile = {"project_id": job.project_id, "status": "profiled", "payload": {k: v for k, v in payload.items() if k not in {"prompt", "input", "output", "content", "messages"}}}
            if self.repository.set_result(job.id, profile, worker_id=self.worker_id, lease_token=self._lease_token) is None:
                raise LeaseLostError(job.id)
            if self.repository.transition(job.id, JobState.COMPLETED, worker_id=self.worker_id, lease_token=self._lease_token) is None:
                raise LeaseLostError(job.id)
            return
        if job.kind == "evaluation":
            self._execute_evaluation(job)
            return
        config = payload.get("config")
        if isinstance(config, dict):
            raw_candidates = config.get("candidates", [])
        elif self.allow_legacy_payload:
            raw_candidates = payload.get("candidates", [])
        else:
            raise ValueError("job payload must contain config.candidates")
        if not isinstance(raw_candidates, list):
            raise ValueError("job payload config.candidates must be a list")
        candidates = [item if isinstance(item, dict) else {"id": str(index), "value": item} for index, item in enumerate(raw_candidates)]
        if self.repository.transition(job.id, JobState.EVALUATING, worker_id=self.worker_id, lease_token=self._lease_token) is None:
            raise LeaseLostError(job.id)
        self.repository.append_event(job.id, "INFO", {"message": "Evaluating candidate configurations", "status": "EVALUATING"}, event_id=f"{job.id}:evaluating")
        fresh = self.repository.get(job.id)
        completed_ids = set((fresh.checkpoint if fresh else {}).get("completed_candidate_ids", []))
        spent = float((fresh.spent_usd if fresh else 0.0) or 0.0)
        persisted = {row.candidate_id for row in (fresh.candidate_results if fresh else [])}
        for index, candidate in enumerate(candidates):
            if self._cancel_if_requested(job, stage="candidate"):
                return
            candidate_id = str(candidate.get("id", index))
            if candidate_id in completed_ids or candidate_id in persisted:
                continue
            self._renew_lease(job, self._active_message)
            self.repository.append_event(job.id, "TESTING", {"message": f"Testing candidate {candidate_id}", "candidateId": candidate_id}, event_id=f"{job.id}:testing:{candidate_id}")
            estimated = float(candidate.get("cost_usd", 0.0) or 0.0)
            if estimated < 0:
                raise ValueError("candidate cost cannot be negative")
            cap = float((fresh.max_experiment_cost_usd if fresh else job.max_experiment_cost_usd) or 0.0)
            if spent + estimated > cap + 1e-12:
                raise SpendLimitExceeded(f"spend cap exceeded: {spent + estimated:.8f} > {cap:.8f}")
            if not self.repository.reserve_spend(job.id, candidate_id, estimated, worker_id=self.worker_id, lease_token=self._lease_token):
                raise SpendLimitExceeded(f"spend cap exceeded while reserving candidate {candidate_id}")
            reserved = True
            try:
                result = self._invoke_with_heartbeat(candidate, fresh or job)
            except Exception:
                self.repository.release_spend_reservation(job.id, candidate_id, worker_id=self.worker_id, lease_token=self._lease_token)
                raise
            if isinstance(result, (int, float)):
                result = {"score": float(result)}
            if not isinstance(result, dict):
                self.repository.release_spend_reservation(job.id, candidate_id, worker_id=self.worker_id, lease_token=self._lease_token)
                raise TypeError("candidate executor must return a mapping or number")
            cost = float(result.get("cost_usd", estimated) or 0.0)
            if cost < 0 or spent + cost > cap + 1e-12:
                self.repository.release_spend_reservation(job.id, candidate_id, worker_id=self.worker_id, lease_token=self._lease_token)
                raise SpendLimitExceeded(f"spend cap exceeded: {spent + cost:.8f} > {cap:.8f}")
            spent += cost
            completed_ids.add(candidate_id)
            checkpoint = {"next_candidate_index": index + 1, "completed_candidate_ids": sorted(completed_ids)}
            if self.repository.record_candidate(job.id, candidate_id, result, cost, checkpoint, worker_id=self.worker_id, lease_token=self._lease_token, reserved_usd=estimated)[1] is False:
                completed_ids.add(candidate_id)
            self.repository.append_event(job.id, "INFO", {"message": f"Candidate {candidate_id} completed", "candidateId": candidate_id, "costUsd": cost}, event_id=f"{job.id}:completed:{candidate_id}")
        if self._cancel_if_requested(job, stage="validation"):
            return
        if self.repository.transition(job.id, JobState.VALIDATING, worker_id=self.worker_id, lease_token=self._lease_token) is None:
            raise LeaseLostError(job.id)
        self.repository.append_event(job.id, "INFO", {"message": "Validating recommendation", "status": "VALIDATING"}, event_id=f"{job.id}:validating")
        final = self.repository.get(job.id)
        if self.repository.checkpoint(job.id, {**(final.checkpoint if final else {}), "next_candidate_index": len(candidates), "completed_candidate_ids": sorted(completed_ids)}, spent_usd=float(final.spent_usd if final else spent), worker_id=self.worker_id, lease_token=self._lease_token) is None:
            raise LeaseLostError(job.id)
        rows = list((final.candidate_results if final else []))
        aggregate = {"candidates": [{"id": row.candidate_id, **row.result} for row in rows], "spent_usd": float(final.spent_usd if final else spent)}
        if self.repository.set_result(job.id, aggregate, worker_id=self.worker_id, lease_token=self._lease_token) is None:
            raise LeaseLostError(job.id)
        if job.kind == "optimization" and rows:
            from services.optimizer.pareto import pareto_frontier, recommend
            from services.optimizer.staged import Candidate
            cfg = config if isinstance(config, dict) else {}
            typed = []
            for row in rows:
                value = row.result
                candidate_config = value.get("config", {})
                if not isinstance(candidate_config, dict):
                    candidate_config = {}
                candidate_config = dict(candidate_config)
                candidate_config.update({k: value[k] for k in ("provider", "model", "parameters") if k in value})
                typed.append(Candidate(id=row.candidate_id, cost_usd=float(value.get("cost_usd", 0.0)), latency_ms=float(value.get("latency_ms", 0.0)), quality=float(value.get("quality", value.get("score", 0.0))), config=candidate_config))
            max_latency = cfg.get("max_p95_latency_ms", cfg.get("max_latency_ms"))
            max_cost = cfg.get("max_cost_usd")
            baseline = cfg.get("baseline")
            baseline_samples = self._quality_samples(baseline)
            if baseline_samples is None and "baseline_quality_samples" in cfg:
                baseline_samples = self._quality_samples({"quality_samples": cfg["baseline_quality_samples"]})
            baseline_quality = baseline.get("quality") if isinstance(baseline, dict) else None
            if baseline_quality is None and baseline_samples:
                baseline_quality = sum(baseline_samples) / len(baseline_samples)

            # Retrospective staged selection records bounded sensitivity,
            # beam, and halving decisions without replaying provider calls.
            from services.optimizer.staged import StagedOptimizer
            staged = StagedOptimizer(evaluate=lambda candidate, _budget: candidate.quality).optimize(
                typed,
                beam_width=int(cfg.get("beam_width", 3)),
                halving_rounds=int(cfg.get("halving_rounds", 2)),
                initial_budget=int(cfg.get("initial_budget", 1)),
                baseline_quality=baseline_quality,
                max_quality_regression=float(cfg.get("max_quality_regression", 0.0)),
                max_latency_ms=max_latency,
                max_cost_usd=max_cost,
            )
            stage_payload = [{"name": stage.name, "candidate_ids": list(stage.candidate_ids), "budget": stage.budget} for stage in staged.stages]
            gate_config = cfg.get("statistical_gate")
            gate_config = gate_config if isinstance(gate_config, dict) else {}
            gate_payload: list[dict[str, Any]] = []
            gate_enabled = baseline_samples is not None
            if gate_enabled:
                from services.optimizer.gates import StatisticalGate
                gate = StatisticalGate(
                    min_quality_delta=float(self._optimization_config_value(cfg, gate_config, "min_quality_delta", 0.0)),
                    alpha=float(self._optimization_config_value(cfg, gate_config, "alpha", 0.05)),
                    min_samples_for_significance=int(self._optimization_config_value(cfg, gate_config, "min_samples_for_significance", 5)),
                    max_quality_regression=float(self._optimization_config_value(cfg, gate_config, "max_quality_regression", 0.0)),
                )
                for row in rows:
                    samples = self._quality_samples(row.result)
                    if samples is None:
                        gate_payload.append({"candidate_id": row.candidate_id, "accepted": False, "reason": "candidate quality_samples are required"})
                        continue
                    result = gate.test(baseline_samples, samples)
                    gate_payload.append({"candidate_id": row.candidate_id, "accepted": result.accepted, "mean_delta": result.mean_delta, "p_value": result.p_value, "reason": result.reason, "confidence_interval": list(result.confidence_interval)})
            gate_by_id = {item["candidate_id"]: item for item in gate_payload}
            stage_ids = set(staged.stages[-1].candidate_ids)
            if gate_enabled:
                stage_pool = [candidate for candidate in typed if candidate.id in stage_ids and gate_by_id.get(candidate.id, {}).get("accepted")]
                if not stage_pool:
                    raise ValueError("no staged candidate passed the statistical quality gate")
            else:
                stage_pool = [candidate for candidate in typed if candidate.id in stage_ids]
            eligible = pareto_frontier(stage_pool, max_latency_ms=max_latency, max_cost_usd=max_cost)
            chosen = recommend(eligible, max_latency_ms=max_latency, max_cost_usd=max_cost)
            recommendation = {"id": chosen.id, "config": chosen.config, "metrics": {"quality": chosen.quality, "latency_ms": chosen.latency_ms, "cost_usd": chosen.cost_usd}, "gates": {"quality": "pass", "latency": "pass", "spend": "pass", "statistical": "pass" if gate_enabled else "not_run"}}
            aggregate["optimization"] = {"stages": stage_payload, "statistical_gate": gate_payload, "selected_stage": staged.stages[-1].name}
            if self.repository.set_result(job.id, aggregate, worker_id=self.worker_id, lease_token=self._lease_token) is None:
                raise LeaseLostError(job.id)
            if self.repository.persist_optimization_result(job.id, recommendation, metadata={"candidate_count": len(rows), "stages": stage_payload, "statistical_gate": gate_payload}, worker_id=self.worker_id, lease_token=self._lease_token) is None:
                raise LeaseLostError(job.id)
            self.repository.append_event(job.id, "SELECTED", {"message": f"Selected candidate {chosen.id}", "candidateId": chosen.id, "status": "COMPLETED"}, event_id=f"{job.id}:selected")
        if self.repository.transition(job.id, JobState.COMPLETED, worker_id=self.worker_id, lease_token=self._lease_token) is None:
            raise LeaseLostError(job.id)
        self.repository.append_event(job.id, "INFO", {"message": "Optimization completed", "status": "COMPLETED"}, event_id=f"{job.id}:completed")

    def _invoke_evaluation_executor(self, case: dict[str, Any], candidate: dict[str, Any], job: dict[str, Any]) -> Any:
        executor = self.evaluation_executor
        # Production adapters expose an explicit execute(candidate, job, case)
        # protocol. Plain test callbacks retain the ergonomic case-first form.
        method = getattr(executor, "execute", None)
        if callable(method):
            return method(candidate, job, case)
        try:
            parameters = list(inspect.signature(executor).parameters.values())
            if len(parameters) == 1:
                return executor(case)
            if len(parameters) == 2:
                return executor(case, candidate)
        except (TypeError, ValueError):
            pass
        return executor(case, candidate, job)

    @staticmethod
    def _normalise_evaluation_result(value: Any) -> dict[str, Any]:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = {"score": value}
        elif not isinstance(value, dict):
            # RunnerResult is intentionally a small dataclass, but accepting
            # attributes keeps the runtime seam independent of its transport.
            value = vars(value) if hasattr(value, "__dict__") else None
        if not isinstance(value, dict):
            raise TypeError("evaluation executor must return a mapping, number, or RunnerResult")
        score_value = value.get("score", value.get("quality"))
        try:
            score = float(score_value)
            latency = float(value.get("latency_ms", 0.0) or 0.0)
        except (TypeError, ValueError) as exc:
            raise ValueError("evaluation score and latency must be numeric") from exc
        if not math.isfinite(score) or not 0.0 <= score <= 1.0:
            raise ValueError("evaluation score must be between 0 and 1")
        if not math.isfinite(latency) or latency < 0:
            raise ValueError("evaluation latency must be a non-negative number")
        passed = value.get("passed")
        if passed is not None and not isinstance(passed, bool):
            raise ValueError("evaluation passed must be boolean when provided")
        usage = value.get("usage") if isinstance(value.get("usage"), dict) else {}
        def nonnegative_number(name: str) -> float:
            try:
                result = float(usage.get(name, value.get(name, 0.0)) or 0.0)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"evaluation {name} must be numeric") from exc
            if not math.isfinite(result) or result < 0:
                raise ValueError(f"evaluation {name} must be non-negative")
            return result
        def nonnegative_int(name: str) -> int:
            result = usage.get(name, value.get(name, 0))
            if isinstance(result, bool) or not isinstance(result, int) or result < 0:
                raise ValueError(f"evaluation {name} must be a non-negative integer")
            return result
        return {
            "score": score,
            "latency_ms": latency,
            "passed": passed,
            "output": value.get("output", "[REDACTED]"),
            "input_tokens": nonnegative_int("input_tokens"),
            "output_tokens": nonnegative_int("output_tokens"),
            "cost_usd": nonnegative_number("cost_usd"),
            "provider_request_id": str(value["provider_request_id"]) if value.get("provider_request_id") else None,
        }

    def _evaluation_aggregate(self, run_id: str) -> dict[str, Any]:
        with self.repository.session_factory() as session:
            rows = list(session.scalars(select(EvalRunCase).where(EvalRunCase.eval_run_id == run_id).order_by(EvalRunCase.ordinal, EvalRunCase.id)).all())
        completed = [row for row in rows if str(row.status).lower() in {"completed", "passed"}]
        scores = [float(row.score) for row in completed if row.score is not None]
        latencies = [float(row.latency_ms) for row in completed if row.latency_ms is not None]
        passed_values = [row.passed for row in completed if row.passed is not None]
        total_cost = 0.0
        input_tokens = output_tokens = 0
        for row in completed:
            evidence = row.evidence if isinstance(row.evidence, dict) else {}
            total_cost += float(evidence.get("cost_usd", 0.0) or 0.0)
            input_tokens += int(evidence.get("input_tokens", 0) or 0)
            output_tokens += int(evidence.get("output_tokens", 0) or 0)
        mean_score = (sum(scores) / len(scores)) if scores else None
        mean_latency = (sum(latencies) / len(latencies)) if latencies else None
        p95_latency = sorted(latencies)[max(0, math.ceil(len(latencies) * 0.95) - 1)] if latencies else None
        passed_count = sum(1 for value in passed_values if value)
        metrics = {
            "total_cases": len(rows),
            "case_count": len(rows),
            "completed_cases": len(completed),
            "completed_case_count": len(completed),
            "failed_cases": sum(1 for row in rows if str(row.status).lower() == "failed"),
            "passed_cases": passed_count,
            "passed_case_count": passed_count,
            "pass_rate": (passed_count / len(passed_values)) if passed_values else None,
            "mean_score": mean_score,
            "average_score": mean_score,
            "mean_latency_ms": mean_latency,
            "average_latency_ms": mean_latency,
            "p95_latency_ms": p95_latency,
            "total_cost_usd": total_cost,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        return metrics

    def _execute_evaluation(self, claimed: Job) -> None:
        if self.repository.transition(claimed.id, JobState.EVALUATING, worker_id=self.worker_id, lease_token=self._lease_token) is None:
            raise LeaseLostError(claimed.id)
        context = self.repository.evaluation_context(claimed.id, worker_id=self.worker_id, lease_token=self._lease_token)
        run_id = context["run_id"]
        candidate = context["candidate"]
        job_wire = {"id": run_id, "organization_id": context["organization_id"], "project_id": context["project_id"]}
        for case in context["cases"]:
            if str(case.get("status", "pending")).lower() in {"completed", "passed"}:
                continue
            execution_key = f"{run_id}:{case['case_id']}"
            self._renew_lease(claimed, self._active_message)
            if self.repository.mark_evaluation_case_running(claimed.id, run_id, case["case_id"], execution_key, worker_id=self.worker_id, lease_token=self._lease_token) is None:
                raise LeaseLostError(claimed.id)
            dispatch_case = dict(case)
            dispatch_case["execution_key"] = execution_key
            try:
                raw = self._invoke_evaluation_executor(dispatch_case, candidate, job_wire)
                result = self._normalise_evaluation_result(raw)
            except Exception as exc:
                # Persist a bounded failure marker so operators can identify the
                # case, while process_once retains its normal retry policy.
                evidence = {"execution_key": execution_key, "error": str(exc)[:1000]}
                if self.repository.persist_evaluation_case(claimed.id, run_id, case["case_id"], status="failed", evidence=evidence, worker_id=self.worker_id, lease_token=self._lease_token) is None:
                    raise LeaseLostError(claimed.id)
                raise
            evidence = {
                "execution_key": execution_key,
                "score": result["score"],
                "latency_ms": result["latency_ms"],
                "output": result["output"] if self._store_content() else "[REDACTED]",
                "input_tokens": result["input_tokens"],
                "output_tokens": result["output_tokens"],
                "cost_usd": result["cost_usd"],
            }
            if result["provider_request_id"]:
                evidence["provider_request_id"] = result["provider_request_id"]
            if self.repository.persist_evaluation_case(claimed.id, run_id, case["case_id"], status="completed", score=result["score"], passed=result["passed"], latency_ms=result["latency_ms"], evidence=evidence, worker_id=self.worker_id, lease_token=self._lease_token) is None:
                raise LeaseLostError(claimed.id)
        metrics = self._evaluation_aggregate(run_id)
        if self.repository.set_result(claimed.id, {"evaluation": metrics}, worker_id=self.worker_id, lease_token=self._lease_token) is None:
            raise LeaseLostError(claimed.id)
        if self.repository.finish_evaluation(claimed.id, run_id, status="completed", aggregate_metrics=metrics, worker_id=self.worker_id, lease_token=self._lease_token) is None:
            raise LeaseLostError(claimed.id)
        if self.repository.transition(claimed.id, JobState.COMPLETED, worker_id=self.worker_id, lease_token=self._lease_token) is None:
            raise LeaseLostError(claimed.id)

    def _invoke_executor(self, candidate: dict[str, Any], job: Job) -> dict[str, Any] | float | int:
        try:
            signature = inspect.signature(self.candidate_executor)
            if len(signature.parameters) == 1:
                return self.candidate_executor(candidate)
        except (TypeError, ValueError):
            pass
        return self.candidate_executor(candidate, job)

    def run_forever(self, *, stop_event: Event | None = None, poll_interval_seconds: float = 1.0, max_iterations: int | None = None) -> None:
        import time
        stop_event = stop_event or Event()
        iterations = 0
        while not stop_event.is_set() and (max_iterations is None or iterations < max_iterations):
            processed = self.process_once()
            iterations += 1
            if not processed and poll_interval_seconds > 0:
                stop_event.wait(poll_interval_seconds)
