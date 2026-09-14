"""Run the bounded Open Deep Research benchmark against AgentPGO role search.

Examples:
  python scripts/run_odr_benchmark.py --mode replay --tasks 20 --search-tasks 50
  python scripts/run_odr_benchmark.py --mode backboard --tasks 1 --model-pool backboard:gpt-luna-5.6

Replay is local and uses historical ODR reports. Backboard mode is live and
requires BACKBOARD_API_KEY in the ODR checkout .env.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.benchmark.open_deep_research import (  # noqa: E402
    DEFAULT_BASELINE, DEFAULT_MODEL_POOL, CallableNodeExecutor,
    load_odr_tasks, load_replay_executor, run_assignment, staged_search,
)
from services.worker.providers import BackboardExecutor, ProviderRequest, RetryPolicy  # noqa: E402


def _load_env(path: Path) -> None:
    if not path.exists(): return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _write_artifact(path: Path, payload: dict[str, Any]) -> None:
    """Persist an aggregate-only result, including bounded failures."""
    path.parent.mkdir(parents=True, exist_ok=True)

    def encode(v: Any) -> Any:
        from dataclasses import asdict, is_dataclass
        if is_dataclass(v): return {k: encode(x) for k, x in asdict(v).items()}
        if isinstance(v, tuple): return [encode(x) for x in v]
        if isinstance(v, dict): return {k: encode(x) for k, x in v.items()}
        return v

    path.write_text(json.dumps(encode(payload), indent=2, sort_keys=True), encoding="utf-8")


def _aggregate_run(run: Any) -> dict[str, Any]:
    """Keep benchmark evidence while excluding prompts, completions, and raw fields."""
    return {
        "mode": run.mode,
        "evidence": run.evidence,
        "assignment": dict(run.assignment),
        "metrics": {
            "quality": run.metrics.quality,
            "mean_cost_usd": run.metrics.mean_cost_usd,
            "p95_latency_ms": run.metrics.p95_latency_ms,
            "task_count": run.metrics.task_count,
            "total_cost_usd": run.metrics.total_cost_usd,
        },
        "tasks": [
            {
                "task_id": task.task_id,
                "proxy_quality": task.proxy_quality,
                "latency_ms": task.latency_ms,
                "cost_usd": task.cost_usd,
                "nodes": [
                    {
                        "node": node.node,
                        "model": node.model,
                        "latency_ms": node.latency_ms,
                        "input_tokens": node.input_tokens,
                        "output_tokens": node.output_tokens,
                        "cost_usd": node.cost_usd,
                    }
                    for node in task.nodes
                ],
            }
            for task in run.tasks
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("replay", "backboard"), default="replay")
    parser.add_argument("--odr-repo", type=Path, default=Path("/home/lenovo/Documents/open_deep_research"))
    parser.add_argument("--artifacts", type=Path, default=Path("/home/lenovo/Documents/open_deep_research/tests/expt_results"))
    parser.add_argument("--tasks", type=int, default=20)
    parser.add_argument("--search-tasks", type=int, default=50)
    parser.add_argument("--beam-width", type=int, default=8)
    parser.add_argument("--halving-rounds", type=int, default=2)
    parser.add_argument("--model-pool", nargs="+", default=list(DEFAULT_MODEL_POOL))
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=None,
        help="per-provider-call timeout (default: BACKBOARD_TIMEOUT_SECONDS or 240 in live mode)",
    )
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "odr-benchmark.json")
    args = parser.parse_args()
    if args.tasks < 1 or args.search_tasks < args.tasks: parser.error("tasks must be positive and search-tasks >= tasks")
    if args.timeout_seconds is not None and not 0 < args.timeout_seconds <= 300:
        parser.error("timeout-seconds must be between 0 and 300")
    pool = tuple(args.model_pool)
    completed_roles: list[str] = []
    try:
        tasks = load_odr_tasks(args.artifacts / "deep_research_bench_gpt-4.1.jsonl", limit=args.search_tasks)
    except Exception as exc:
        payload = {"status": "failed", "mode": args.mode, "evidence": "aggregate-only bounded run; no prompts or outputs persisted", "model_pool": list(pool), "completed_roles": completed_roles, "error_category": type(exc).__name__, "error": str(exc)[:500]}
        _write_artifact(args.output, payload)
        print(json.dumps({"status": "failed", "mode": args.mode, "output": str(args.output), "tasks": args.tasks}, indent=2))
        return 2
    _write_artifact(args.output, {
        "status": "running",
        "mode": args.mode,
        "evidence": "aggregate-only bounded run; no prompts or outputs persisted",
        "model_pool": list(pool),
        "completed_roles": completed_roles,
    })
    try:
        if args.mode == "replay":
            executor = load_replay_executor(args.artifacts, pool)
            result = staged_search(tasks, executor, DEFAULT_BASELINE, pool, early_tasks=args.tasks, search_tasks=args.search_tasks, beam_width=args.beam_width, halving_rounds=args.halving_rounds, replay=executor)
            payload = {"status": "completed", "mode": "replay", "evidence": "historical ODR report replay; proxy quality only", "model_pool": list(pool), **result}
        else:
            _load_env(args.odr_repo / ".env")
            key = os.getenv("BACKBOARD_API_KEY")
            if not key: raise RuntimeError("blocked: BACKBOARD_API_KEY is not set in the ODR .env")
            if any(not model.startswith("backboard:") for model in pool): raise RuntimeError("backboard mode requires a backboard-only --model-pool")
            configured_timeout = args.timeout_seconds
            if configured_timeout is None:
                configured_timeout = float(os.getenv("BACKBOARD_TIMEOUT_SECONDS", "240"))
            if not 0 < configured_timeout <= 300:
                raise RuntimeError("BACKBOARD_TIMEOUT_SECONDS must be between 0 and 300")
            provider = BackboardExecutor(
                api_key=key,
                base_url=os.getenv("BACKBOARD_BASE_URL"),
                llm_provider=os.getenv("BACKBOARD_LLM_PROVIDER", "openai"),
                timeout_seconds=configured_timeout,
                retry_policy=RetryPolicy(max_attempts=1, backoff_seconds=0, timeout_seconds=configured_timeout),
            )
            def call(node: str, model: str, prompt: str):
                response = provider.execute(ProviderRequest(provider="backboard", model=model.split(":", 1)[-1], prompt=prompt, temperature=0.0))
                completed_roles.append(node)
                return response.text, response.latency_ms, response.input_tokens, response.output_tokens, None
            executor = CallableNodeExecutor(call)
            run = run_assignment(tasks[:args.tasks], DEFAULT_BASELINE | {node: pool[0] for node in DEFAULT_BASELINE}, executor, mode="live", evidence="live Backboard role pipeline")
            payload = {"status": "completed", "mode": "backboard", "evidence": "actual Backboard requests through four ODR roles", "model_pool": list(pool), "timeout_seconds": configured_timeout, "completed_roles": completed_roles, "baseline": _aggregate_run(run)}
    except Exception as exc:
        payload = {
            "status": "failed",
            "mode": args.mode,
            "evidence": "aggregate-only bounded run; no prompts or outputs persisted",
            "model_pool": list(pool),
            "completed_roles": completed_roles,
            "error_category": type(exc).__name__,
            "error": str(exc)[:500],
        }
        _write_artifact(args.output, payload)
        print(json.dumps({"status": "failed", "mode": args.mode, "output": str(args.output), "tasks": args.tasks}, indent=2))
        return 2
    _write_artifact(args.output, payload)
    print(json.dumps({"status": payload["status"], "mode": args.mode, "output": str(args.output), "tasks": args.tasks}, indent=2))
    return 0

if __name__ == "__main__": raise SystemExit(main())
