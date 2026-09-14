from __future__ import annotations

import pytest

import json
import os
from pathlib import Path
import subprocess
import sys

from benchmark.open_deep_research import BenchmarkMetrics, BenchmarkRun, NodeObservation, TaskObservation
from scripts.run_odr_benchmark import _aggregate_run


ROOT = Path(__file__).resolve().parents[2]
TASK_ARTIFACTS = Path("/home/lenovo/Documents/open_deep_research/tests/expt_results")


def test_live_runner_writes_aggregate_failure_artifact(tmp_path: Path) -> None:
    output = tmp_path / "odr-failure.json"
    env = os.environ.copy()
    env.pop("BACKBOARD_API_KEY", None)
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "run_odr_benchmark.py"),
            "--mode",
            "backboard",
            "--odr-repo",
            str(tmp_path / "no-env"),
            "--artifacts",
            str(TASK_ARTIFACTS),
            "--tasks",
            "1",
            "--search-tasks",
            "1",
            "--model-pool",
            "backboard:claude-sonnet-4-6",
            "--output",
            str(output),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "failed"
    assert payload["mode"] == "backboard"
    assert payload["completed_roles"] == []
    assert "prompt" not in payload
    assert "output" not in payload


def test_aggregate_run_excludes_prompt_and_completion_content() -> None:
    run = BenchmarkRun(
        mode="live",
        evidence="test",
        assignment={"summarizer": "backboard:test"},
        metrics=BenchmarkMetrics(0.5, None, 10.0, 1, None),
        tasks=(
            TaskObservation(
                task_id="1",
                output="private completion",
                proxy_quality=0.5,
                latency_ms=10.0,
                cost_usd=None,
                nodes=(NodeObservation("summarizer", "backboard:test", 10.0, text="private node output"),),
            ),
        ),
    )
    aggregate = _aggregate_run(run)
    assert "output" not in aggregate
    assert "text" not in aggregate["tasks"][0]
    assert "text" not in aggregate["tasks"][0]["nodes"][0]
