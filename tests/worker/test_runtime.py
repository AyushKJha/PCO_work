from __future__ import annotations

import json

import pytest

from apps.api.db import create_session_factory, create_tables
from apps.api.models import Job, Organization
from services.worker.providers import ProviderExecutor, ProviderRequest, ProviderResponse, RetryPolicy
from services.worker.queue import InMemoryQueue, SQSQueueConsumer, SQSQueuePublisher
from services.worker.runtime import JobRepository, JobState, WorkerRuntime


def test_sqs_publisher_and_consumer_use_json_message_contract() -> None:
    calls: list[dict] = []

    class Client:
        def send_message(self, **kwargs):
            calls.append(kwargs)
            return {"MessageId": "m-1"}

        def receive_message(self, **kwargs):
            return {"Messages": []}

    publisher = SQSQueuePublisher("https://sqs.example/jobs", client=Client())
    assert publisher.publish("job-1", {"kind": "optimization"}) == "m-1"
    assert calls[0]["QueueUrl"].endswith("/jobs")
    assert json.loads(calls[0]["MessageBody"]) == {"job_id": "job-1", "kind": "optimization"}


def test_repository_claim_is_atomic_and_only_queued_jobs_are_claimable(tmp_path) -> None:
    factory = create_session_factory(f"sqlite:///{tmp_path / 'jobs.db'}")
    create_tables(factory)
    with factory.begin() as session:
        organization = Organization(name="Acme")
        session.add(organization)
        session.flush()
        job = Job(organization_id=organization.id, kind="optimization", payload={})
        session.add(job)
        session.flush()
        job_id = job.id

    repository = JobRepository(factory)
    claimed = repository.claim_next("worker-a", lease_seconds=30)
    assert claimed is not None and claimed.id == job_id
    assert claimed.status == "running"
    assert repository.claim_next("worker-b", lease_seconds=30) is None


def test_worker_checkpoints_candidates_and_is_idempotent(tmp_path) -> None:
    factory = create_session_factory(f"sqlite:///{tmp_path / 'jobs.db'}")
    create_tables(factory)
    with factory.begin() as session:
        organization = Organization(name="Acme")
        session.add(organization)
        session.flush()
        job = Job(
            organization_id=organization.id,
            kind="optimization",
            payload={"config": {"candidates": [{"id": "c-1", "cost_usd": 0.25}, {"id": "c-2", "cost_usd": 0.25}]}},
            max_experiment_cost_usd=1,
        )
        session.add(job)
        session.flush()
        job_id = job.id

    queue = InMemoryQueue()
    queue.publish(job_id, {})
    runtime = WorkerRuntime(factory, queue, worker_id="worker-a")
    assert runtime.process_once() is True
    assert runtime.process_once() is False
    with factory() as session:
        row = session.get(Job, job_id)
        assert row is not None
        assert row.status == "completed"
        assert row.checkpoint["completed_candidate_ids"] == ["c-1", "c-2"]
        assert len(row.candidate_results) == 2


def test_worker_wires_staged_selection_and_paired_statistical_gate(tmp_path) -> None:
    factory = create_session_factory(f"sqlite:///{tmp_path / 'optimizer.db'}")
    create_tables(factory)
    with factory.begin() as session:
        organization = Organization(name="Acme")
        session.add(organization)
        session.flush()
        job = Job(
            organization_id=organization.id,
            kind="optimization",
            payload={
                "config": {
                    "baseline": {"id": "baseline", "quality": 0.0, "quality_samples": [0, 0, 0, 0, 0]},
                    "beam_width": 2,
                    "halving_rounds": 1,
                    "initial_budget": 1,
                    "statistical_gate": {"min_quality_delta": 0.1, "min_samples_for_significance": 5},
                    "candidates": [{"id": "good", "cost_usd": 0.1}, {"id": "neutral", "cost_usd": 0.1}],
                }
            },
            max_experiment_cost_usd=1,
        )
        session.add(job)
        session.flush()
        job_id = job.id

    queue = InMemoryQueue()
    queue.publish(job_id, {})

    def execute(candidate: dict) -> dict:
        samples = [1, 1, 1, 1, 1] if candidate["id"] == "good" else [0, 0, 0, 0, 0]
        return {"quality": sum(samples) / len(samples), "quality_samples": samples, "cost_usd": 0.1, "latency_ms": 10}

    runtime = WorkerRuntime(factory, queue, worker_id="optimizer-worker", candidate_executor=execute)
    assert runtime.process_once() is True

    with factory() as session:
        row = session.get(Job, job_id)
        assert row is not None
        assert row.status == JobState.COMPLETED.value
        assert [stage["name"] for stage in row.result["optimization"]["stages"]] == [
            "sensitivity",
            "beam",
            "successive_halving",
        ]
        gates = {item["candidate_id"]: item for item in row.result["optimization"]["statistical_gate"]}
        assert gates["good"]["accepted"] is True
        assert gates["neutral"]["accepted"] is False
        assert row.result["recommendation"]["id"] == "good"
        assert row.result["recommendation"]["gates"]["statistical"] == "pass"
        assert row.optimization_result is not None
        assert row.optimization_result.metadata_json["stages"] == row.result["optimization"]["stages"]


def test_worker_moves_poison_message_to_dlq_after_bounded_receives(tmp_path) -> None:
    factory = create_session_factory(f"sqlite:///{tmp_path / 'jobs.db'}")
    create_tables(factory)
    queue = InMemoryQueue()
    queue.publish("missing-job", {})
    runtime = WorkerRuntime(factory, queue, worker_id="worker-a", max_receive_count=1)
    assert runtime.process_once() is False
    assert len(queue.dead_letters) == 1


def test_worker_stops_before_exceeding_spend_cap(tmp_path) -> None:
    factory = create_session_factory(f"sqlite:///{tmp_path / 'jobs.db'}")
    create_tables(factory)
    with factory.begin() as session:
        organization = Organization(name="Acme")
        session.add(organization)
        session.flush()
        job = Job(
            organization_id=organization.id,
            kind="optimization",
            payload={"config": {"candidates": [{"id": "too-expensive", "cost_usd": 1.01}]}},
            max_experiment_cost_usd=1,
        )
        session.add(job)
        session.flush()
        job_id = job.id
    queue = InMemoryQueue()
    queue.publish(job_id, {})
    runtime = WorkerRuntime(factory, queue, worker_id="worker-a")
    assert runtime.process_once() is True
    with factory() as session:
        assert session.get(Job, job_id).status == "failed"


def test_legacy_candidates_require_explicit_runtime_opt_in(tmp_path) -> None:
    factory = create_session_factory(f"sqlite:///{tmp_path / 'jobs.db'}")
    create_tables(factory)
    with factory.begin() as session:
        organization = Organization(name="Acme")
        session.add(organization)
        session.flush()
        job = Job(organization_id=organization.id, kind="optimization", payload={"candidates": [{"id": "old"}]})
        session.add(job)
        session.flush()
        job_id = job.id
    queue = InMemoryQueue()
    queue.publish(job_id, {})
    runtime = WorkerRuntime(factory, queue, worker_id="worker-a", allow_legacy_payload=True)
    assert runtime.process_once() is True
    with factory() as session:
        assert session.get(Job, job_id).status == JobState.COMPLETED.value


def test_worker_persists_aggregate_result_state(tmp_path) -> None:
    factory = create_session_factory(f"sqlite:///{tmp_path / 'jobs.db'}")
    create_tables(factory)
    with factory.begin() as session:
        organization = Organization(name="Acme")
        session.add(organization)
        session.flush()
        job = Job(organization_id=organization.id, kind="optimization", payload={"config": {"candidates": [{"id": "c-1"}]}})
        session.add(job)
        session.flush()
        job_id = job.id
    queue = InMemoryQueue()
    queue.publish(job_id, {})
    runtime = WorkerRuntime(factory, queue, worker_id="worker-a", candidate_executor=lambda candidate: {"score": 0.9})
    assert runtime.process_once() is True
    with factory() as session:
        result = session.get(Job, job_id).result
        assert result["candidates"][0]["id"] == "c-1"
        assert result["candidates"][0]["score"] == 0.9


def test_repository_lease_can_be_renewed_and_stale_token_is_fenced(tmp_path) -> None:
    factory = create_session_factory(f"sqlite:///{tmp_path / 'jobs.db'}")
    create_tables(factory)
    with factory.begin() as session:
        organization = Organization(name="Acme")
        session.add(organization)
        session.flush()
        job = Job(organization_id=organization.id, kind="optimization", payload={"config": {"candidates": []}})
        session.add(job)
        session.flush()
        job_id = job.id
    repository = JobRepository(factory)
    claimed = repository.claim(job_id, "worker-a", lease_seconds=1)
    assert claimed is not None
    old_token = claimed.claim_expires_at
    renewed = repository.renew_lease(job_id, "worker-a", lease_seconds=30, lease_token=old_token)
    assert renewed is not None and renewed.claim_expires_at > old_token
    assert repository.transition(job_id, JobState.EVALUATING, worker_id="worker-a", lease_token=old_token) is None
    assert repository.transition(job_id, JobState.EVALUATING, worker_id="worker-a", lease_token=renewed.claim_expires_at) is not None


def test_repository_spend_reservation_is_atomic_and_released_on_failure(tmp_path) -> None:
    factory = create_session_factory(f"sqlite:///{tmp_path / 'jobs.db'}")
    create_tables(factory)
    with factory.begin() as session:
        organization = Organization(name="Acme")
        session.add(organization)
        session.flush()
        job = Job(organization_id=organization.id, kind="optimization", max_experiment_cost_usd=1, payload={"config": {"candidates": []}})
        session.add(job)
        session.flush()
        job_id = job.id
    repository = JobRepository(factory)
    claimed = repository.claim(job_id, "worker-a")
    assert claimed is not None
    assert repository.reserve_spend(job_id, "c-1", 0.75, worker_id="worker-a", lease_token=claimed.claim_expires_at)
    assert not repository.reserve_spend(job_id, "c-2", 0.5, worker_id="worker-a", lease_token=claimed.claim_expires_at)
    assert repository.release_spend_reservation(job_id, "c-1", worker_id="worker-a", lease_token=claimed.claim_expires_at)
    assert repository.reserve_spend(job_id, "c-2", 0.5, worker_id="worker-a", lease_token=claimed.claim_expires_at)


def test_sqs_visibility_extension_uses_change_message_visibility() -> None:
    calls: list[dict] = []

    class Client:
        def change_message_visibility(self, **kwargs):
            calls.append(kwargs)

    consumer = SQSQueueConsumer("https://sqs.example/jobs", client=Client())
    from services.worker.queue import QueueMessage
    message = QueueMessage("m", "r", {"job_id": "j"})
    consumer.extend_visibility(message, 99999)
    assert calls[0]["VisibilityTimeout"] == 43200


def test_sqs_dlq_fails_closed_when_no_dlq_is_configured() -> None:
    class Client:
        def delete_message(self, **kwargs):
            raise AssertionError("source message must not be acknowledged")

    consumer = SQSQueueConsumer("https://sqs.example/jobs", client=Client())
    from services.worker.queue import QueueMessage
    message = QueueMessage("m", "r", {"job_id": "j"})
    with pytest.raises(RuntimeError, match="DLQ"):
        consumer.move_to_dlq(message)


def test_provider_timeout_is_bounded_and_classified_as_retryable() -> None:
    def transport(_request):
        import time
        time.sleep(0.05)
        return ProviderResponse("late")

    executor = ProviderExecutor(transport=transport, retry_policy=RetryPolicy(max_attempts=1, timeout_seconds=0.001))
    with pytest.raises(RuntimeError, match="timeout"):
        executor.execute(ProviderRequest("openai", "gpt", "hello"))
