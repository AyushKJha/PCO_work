"""End-to-end tests for the public ``agentpgo`` command surface."""

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "cli", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )


def test_init_creates_project_config_and_is_idempotent(tmp_path: Path) -> None:
    first = run_cli("init", "--project", "checkout-agent", cwd=tmp_path)
    assert first.returncode == 0, first.stderr

    config_path = tmp_path / ".agentpgo" / "config.json"
    config = json.loads(config_path.read_text())
    assert config["project"] == "checkout-agent"
    assert config["api_url"] == "http://localhost:8000"
    assert config["schema_version"] == 1

    second = run_cli("init", "--project", "checkout-agent", cwd=tmp_path)
    assert second.returncode == 0, second.stderr
    assert json.loads(config_path.read_text()) == config


def test_profile_and_eval_import_validate_input_and_write_local_artifacts(tmp_path: Path) -> None:
    assert run_cli("init", "--project", "demo", cwd=tmp_path).returncode == 0
    trace_path = tmp_path / "traces.jsonl"
    trace_path.write_text('{"trace_id":"t-1","latency_ms":42,"cost_usd":0.01}\n')
    eval_path = tmp_path / "eval.json"
    eval_path.write_text(json.dumps([{"input": "hello", "expected": "world"}]))

    profile = run_cli("profile", str(trace_path), cwd=tmp_path)
    assert profile.returncode == 0, profile.stderr
    profile_result = json.loads(profile.stdout)
    assert profile_result["trace_count"] == 1
    assert (tmp_path / ".agentpgo" / "profiles" / "latest.json").exists()

    imported = run_cli("eval", "import", str(eval_path), cwd=tmp_path)
    assert imported.returncode == 0, imported.stderr
    import_result = json.loads(imported.stdout)
    assert import_result["case_count"] == 1
    assert (tmp_path / ".agentpgo" / "evals" / "latest.json").exists()


def test_optimize_and_export_require_profile_and_emit_policy(tmp_path: Path) -> None:
    assert run_cli("init", "--project", "demo", cwd=tmp_path).returncode == 0
    trace_path = tmp_path / "traces.json"
    trace_path.write_text(json.dumps({"traces": [{"trace_id": "t-1"}]}))
    assert run_cli("profile", str(trace_path), cwd=tmp_path).returncode == 0

    optimized = run_cli("optimize", cwd=tmp_path)
    assert optimized.returncode == 0, optimized.stderr
    result = json.loads(optimized.stdout)
    assert result["status"] == "ready"
    assert (tmp_path / ".agentpgo" / "policy.json").exists()

    output_path = tmp_path / "policy.yaml"
    exported = run_cli("export", "--output", str(output_path), cwd=tmp_path)
    assert exported.returncode == 0, exported.stderr
    assert "project: demo" in output_path.read_text()
    assert "status: ready" in output_path.read_text()


def test_invalid_input_returns_actionable_error(tmp_path: Path) -> None:
    assert run_cli("init", cwd=tmp_path).returncode == 0
    invalid = tmp_path / "bad.json"
    invalid.write_text("not-json")

    result = run_cli("profile", str(invalid), cwd=tmp_path)
    assert result.returncode == 2
    assert "valid JSON or JSONL" in result.stderr
