from __future__ import annotations

import pytest
from pathlib import Path

from benchmark.open_deep_research import (
    DEFAULT_BASELINE, NODE_SEQUENCE, HistoricalReplayExecutor, load_odr_tasks,
    run_assignment, staged_search,
)


def test_load_odr_historical_tasks():
    artifact = Path("/home/lenovo/Documents/open_deep_research/tests/expt_results/deep_research_bench_gpt-4.1.jsonl")
    if not artifact.exists(): pytest.skip("ODR historical artifacts are local-only")
    tasks = load_odr_tasks(artifact, limit=20)
    assert len(tasks) == 20
    assert tasks[0].prompt
    assert tasks[0].reference_article


def test_replay_assignment_records_four_requested_roles():
    artifact = Path("/home/lenovo/Documents/open_deep_research/tests/expt_results/deep_research_bench_gpt-4.1.jsonl")
    if not artifact.exists(): pytest.skip("ODR historical artifacts are local-only")
    models = ("openai:gpt-4.1-mini", "openai:gpt-4.1")
    executor = HistoricalReplayExecutor({m: {"57": "report"} for m in models}, {m: (1.0, 2.0) for m in models})
    task = load_odr_tasks(artifact, limit=1)[0]
    run = run_assignment((task,), DEFAULT_BASELINE, executor, mode="replay", evidence="test", replay=executor)
    assert tuple(n.node for n in run.tasks[0].nodes) == NODE_SEQUENCE
    assert run.metrics.task_count == 1
    assert run.metrics.total_cost_usd is not None


def test_staged_search_is_bounded_and_reports_gate():
    artifact = Path("/home/lenovo/Documents/open_deep_research/tests/expt_results/deep_research_bench_gpt-4.1.jsonl")
    if not artifact.exists(): pytest.skip("ODR historical artifacts are local-only")
    models = ("openai:gpt-4.1-mini", "openai:gpt-4.1")
    tasks = load_odr_tasks(artifact, limit=20)
    executor = HistoricalReplayExecutor({m: {t.task_id: (t.reference_article or "") for t in tasks} for m in models}, {m: (1.0, 2.0) for m in models})
    result = staged_search(tasks, executor, DEFAULT_BASELINE, models, early_tasks=5, search_tasks=10, beam_width=2, halving_rounds=1, replay=executor)
    assert result["evaluated_candidate_count"] <= 9
    assert len(result["sensitivity"]) == len(NODE_SEQUENCE)
    assert result["statistical_gate"]
    assert result["quality_semantics"].startswith("proxy")
