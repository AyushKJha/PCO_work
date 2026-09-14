from __future__ import annotations

import hashlib
import hmac
import json
from sqlalchemy import select

from apps.api.db import create_session_factory, create_tables
from apps.api.models import EvalCase, EvalDataset, EvalRun, EvalRunCase, Job, Organization, Project
from services.worker.queue import InMemoryQueue
from services.worker.runner import RunnerExecutor, RunnerResult
from services.worker.runtime import JobState, WorkerRuntime


def test_runner_callback_uses_explicit_case_candidate_job_order() -> None:
    calls: list[tuple[dict, dict, dict]] = []

    def transport(method: str, url: str, body: dict, headers: dict) -> dict:
        del method, url, headers
        return {"quality": 0.75, "latency_ms": 4.0, "passed": True}

    executor = RunnerExecutor(
        "https://runner.example/v1/tasks",
        signing_secret="secret",
        transport=transport,
    )

    def callback(case: dict, candidate: dict, job: dict) -> RunnerResult:
        calls.append((case, candidate, job))
        return executor.execute(candidate, job, case)

    case = {"case_id": "case-1", "execution_key": "run-1:case-1"}
    candidate = {"model": "safe/model"}
    job = {"id": "run-1"}
    callback(case, candidate, job)

    assert calls == [(case, candidate, job)]


def test_runner_rejects_missing_or_nonfinite_quality_and_latency() -> None:
    invalid_payloads = (
        {"latency_ms": 1.0},
        {"quality": float("nan"), "latency_ms": 1.0},
        {"quality": 0.5},
        {"quality": 0.5, "latency_ms": float("inf")},
    )

    for payload in invalid_payloads:
        executor = RunnerExecutor(
            "https://runner.example/v1/tasks",
            signing_secret="secret",
            transport=lambda *_args, payload=payload: payload,
        )
        try:
            executor.execute(
                {"model": "safe/model"},
                {"id": "run-1"},
                {"case_id": "case-1"},
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid payload was accepted: {payload!r}")


def test_runner_rejects_fractional_tokens_negative_cost_and_invalid_passed() -> None:
    invalid_payloads = (
        {"quality": 0.5, "latency_ms": 1.0, "usage": {"input_tokens": 1.5}},
        {"quality": 0.5, "latency_ms": 1.0, "usage": {"cost_usd": -0.01}},
        {"quality": 0.5, "latency_ms": 1.0, "passed": "true"},
    )
    for payload in invalid_payloads:
        executor = RunnerExecutor(
            "https://runner.example/v1/tasks",
            signing_secret="secret",
            transport=lambda *_args, payload=payload: payload,
        )
        try:
            executor.execute(
                {"model": "safe/model"},
                {"id": "run-1"},
                {"case_id": "case-1"},
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid payload was accepted: {payload!r}")


def test_runner_executor_signs_short_lived_task_and_validates_result() -> None:
    calls: list[tuple[str, dict, dict]] = []

    def transport(method: str, url: str, body: dict, headers: dict) -> dict:
        calls.append((url, body, headers))
        return {"quality": 0.9, "usage": {"input_tokens": 3, "output_tokens": 2, "cost_usd": 0.01}, "latency_ms": 12.5}

    executor = RunnerExecutor("https://runner.example/v1/tasks", signing_secret="secret", transport=transport, clock=lambda: 1_700_000_000)
    result = executor.execute(
        {"model": "safe/model"},
        {"id": "run-1", "organization_id": "org-1", "project_id": "project-1"},
        {"case_id": "case-1", "input": {"question": "hello"}, "expected": "world"},
    )
    url, body, headers = calls[0]
    assert url.endswith("/v1/tasks")
    assert body["execution_key"] == "run-1:case-1"
    assert body["expires_at"] == 1_700_000_300
    canonical = json.dumps(body, separators=(",", ":"), sort_keys=True).encode()
    expected = hmac.new(b"secret", b"1700000000." + canonical, hashlib.sha256).hexdigest()
    assert headers["X-AgentPGO-Runner-Signature"] == "sha256=" + expected
    assert isinstance(result, RunnerResult)
    assert result.quality == 0.9


def test_worker_executes_eval_cases_persists_results_and_skips_completed_case_on_restart(tmp_path) -> None:
    factory = create_session_factory(f"sqlite:///{tmp_path / 'eval.db'}")
    create_tables(factory)
    with factory.begin() as session:
        org = Organization(name="Acme")
        session.add(org)
        session.flush()
        project = Project(name="Research", slug="research", organization_id=org.id)
        session.add(project)
        session.flush()
        dataset = EvalDataset(organization_id=org.id, project_id=project.id, name="suite", version=1)
        session.add(dataset)
        session.flush()
        session.add(EvalCase(dataset_id=dataset.id, case_id="case-1", input_data={"q": "hello"}, expected="world", ordinal=0))
        run = EvalRun(organization_id=org.id, project_id=project.id, eval_suite_id=dataset.id, candidate_config={"model": "safe/model"})
        run.cases = [EvalRunCase(case_id="case-1", ordinal=0)]
        session.add(run)
        session.flush()
        session.add(Job(id=run.id, organization_id=org.id, project_id=project.id, kind="evaluation", dataset_id=dataset.id, payload={"kind": "evaluation", "eval_run_id": run.id, "eval_suite_id": dataset.id, "config": {"model": "safe/model"}}))
        run_id = run.id

    calls: list[str] = []

    def evaluate(case, candidate, _job):
        calls.append(case["case_id"])
        return {"quality": 1.0, "usage": {"input_tokens": 2, "output_tokens": 1, "cost_usd": 0.01}, "latency_ms": 8.0, "passed": True, "output": "world"}

    queue = InMemoryQueue()
    queue.publish(run_id, {"kind": "evaluation"})
    first = WorkerRuntime(factory, queue, worker_id="worker-a", evaluation_executor=evaluate)
    assert first.process_once() is True
    with factory() as session:
        run = session.get(EvalRun, run_id)
        job = session.get(Job, run_id)
        assert run.status == "completed"
        assert run.cases[0].status == "completed"
        assert run.cases[0].score == 1.0
        assert run.cases[0].evidence["output"] == "[REDACTED]"
        assert job.status == JobState.COMPLETED.value

    queue.publish(run_id, {"kind": "evaluation"})
    second = WorkerRuntime(factory, queue, worker_id="worker-b", evaluation_executor=evaluate)
    assert second.process_once() is False
    assert calls == ["case-1"]


def test_worker_persists_running_key_before_call_and_reuses_it_after_crash(tmp_path) -> None:
    factory = create_session_factory(f"sqlite:///{tmp_path / 'eval-retry.db'}")
    create_tables(factory)
    with factory.begin() as session:
        org = Organization(name="Acme")
        session.add(org)
        session.flush()
        project = Project(name="Research", slug="research", organization_id=org.id)
        session.add(project)
        session.flush()
        dataset = EvalDataset(organization_id=org.id, project_id=project.id, name="suite", version=1)
        session.add(dataset)
        session.flush()
        session.add(EvalCase(dataset_id=dataset.id, case_id="case-1", input_data={"q": "hello"}, expected="world", ordinal=0))
        run = EvalRun(organization_id=org.id, project_id=project.id, eval_suite_id=dataset.id, candidate_config={"model": "safe/model"})
        run.cases = [EvalRunCase(case_id="case-1", ordinal=0)]
        session.add(run)
        session.flush()
        session.add(Job(id=run.id, organization_id=org.id, project_id=project.id, kind="evaluation", dataset_id=dataset.id, payload={"kind": "evaluation", "eval_run_id": run.id, "eval_suite_id": dataset.id, "config": {"model": "safe/model"}}))
        run_id = run.id

    calls: list[str] = []

    def evaluate(case, _candidate, _job):
        calls.append(case["execution_key"])
        with factory() as session:
            row = session.scalar(
                select(EvalRunCase).where(
                    EvalRunCase.eval_run_id == run_id,
                    EvalRunCase.case_id == "case-1",
                )
            )
            assert row is not None
            assert row.status == "running"
            assert row.evidence["execution_key"] == case["execution_key"]
        if len(calls) == 1:
            raise RuntimeError("simulated worker crash after gateway execution")
        return {"quality": 1.0, "usage": {"input_tokens": 2, "output_tokens": 1, "cost_usd": 0.01}, "latency_ms": 8.0, "passed": True, "output": "world"}

    queue = InMemoryQueue()
    queue.publish(run_id, {"kind": "evaluation"})
    first = WorkerRuntime(factory, queue, worker_id="worker-a", evaluation_executor=evaluate)
    assert first.process_once() is True
    with factory() as session:
        row = session.scalar(select(EvalRunCase).where(EvalRunCase.eval_run_id == run_id, EvalRunCase.case_id == "case-1"))
        assert row is not None
        assert row.status == "failed"
        assert row.evidence["execution_key"] == f"{run_id}:case-1"

    second = WorkerRuntime(factory, queue, worker_id="worker-b", evaluation_executor=evaluate)
    assert second.process_once() is True
    assert calls == [f"{run_id}:case-1", f"{run_id}:case-1"]
    with factory() as session:
        row = session.scalar(select(EvalRunCase).where(EvalRunCase.eval_run_id == run_id, EvalRunCase.case_id == "case-1"))
        assert row is not None
        assert row.status == "completed"
        assert row.evidence["output"] == "[REDACTED]"
