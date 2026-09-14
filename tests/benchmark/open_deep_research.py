"""Bounded Open Deep Research role benchmark for AgentPGO.

Replay mode uses historical ODR JSONL artifacts and labels quality as a proxy.
Live mode uses a supplied node executor (for example Backboard) and never
pretends that proxy quality is the Deep Research Bench RACE score.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
import json
import math
from pathlib import Path
import statistics
from typing import Any, Callable, Iterable, Mapping, Protocol

from services.optimizer.gates import StatisticalGate

NODE_SEQUENCE = ("summarizer", "researcher", "compressor", "final_report")
DEFAULT_BASELINE = {
    "summarizer": "openai:gpt-4.1-mini",
    "researcher": "openai:gpt-4.1",
    "compressor": "openai:gpt-4.1",
    "final_report": "openai:gpt-4.1",
}
DEFAULT_MODEL_POOL = (
    "openai:gpt-4.1-mini", "openai:gpt-4.1", "openai:gpt-5",
    "anthropic:claude-sonnet-4-20250514", "anthropic:claude-opus-4-1-20250805",
    "google:gemini-2.5-flash", "google:gemini-2.5-pro", "backboard:gpt-luna-5.6",
)

@dataclass(frozen=True)
class ResearchTask:
    task_id: str
    prompt: str
    reference_article: str | None = None

@dataclass(frozen=True)
class NodeObservation:
    node: str
    model: str
    latency_ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    text: str = ""

@dataclass(frozen=True)
class TaskObservation:
    task_id: str
    output: str
    proxy_quality: float | None
    latency_ms: float
    cost_usd: float | None
    nodes: tuple[NodeObservation, ...]

@dataclass(frozen=True)
class BenchmarkMetrics:
    quality: float | None
    mean_cost_usd: float | None
    p95_latency_ms: float
    task_count: int
    total_cost_usd: float | None

@dataclass(frozen=True)
class BenchmarkRun:
    mode: str
    evidence: str
    assignment: dict[str, str]
    metrics: BenchmarkMetrics
    tasks: tuple[TaskObservation, ...]

class NodeExecutor(Protocol):
    def execute(self, node: str, model: str, prompt: str) -> NodeObservation: ...

def _tokens(text: str) -> int: return max(1, len(text.split()))
def _jaccard(left: str, right: str) -> float:
    a, b = set(left.casefold().split()), set(right.casefold().split())
    return len(a & b) / len(a | b) if a and b else 0.0
def _p95(values: Iterable[float]) -> float:
    v = tuple(values)
    return v[0] if len(v) < 2 else statistics.quantiles(v, n=20, method="inclusive")[18]
def _candidate_id(assignment: Mapping[str, str]) -> str:
    return "|".join(f"{node}={assignment[node]}" for node in NODE_SEQUENCE)

def load_odr_tasks(path: str | Path, *, limit: int | None = None) -> tuple[ResearchTask, ...]:
    tasks = []
    for line_no, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip(): continue
        try:
            row = json.loads(line)
            tasks.append(ResearchTask(str(row["id"]), str(row["prompt"]), str(row["article"]) if row.get("article") is not None else None))
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"invalid ODR artifact row {line_no}") from exc
        if limit is not None and len(tasks) >= limit: break
    if not tasks: raise ValueError(f"no ODR tasks found in {path}")
    return tuple(tasks)

class HistoricalReplayExecutor:
    """Historical final-report replay; quality is a proxy, never RACE."""
    def __init__(self, artifacts: Mapping[str, Mapping[str, str]], prices: Mapping[str, tuple[float, float]] | None = None) -> None:
        self.artifacts = {k: dict(v) for k, v in artifacts.items()}
        self.prices = dict(prices or {})
    def execute(self, node: str, model: str, prompt: str) -> NodeObservation:
        output = f"replay:{node}:{model}:{prompt[:32]}"
        inp, out = _tokens(prompt), _tokens(output)
        price_in, price_out = self.prices.get(model, (0.0, 0.0))
        cost = inp / 1_000_000 * price_in + out / 1_000_000 * price_out
        return NodeObservation(node, model, float(25 + (hash(model) % 17)), inp, out, cost, output)
    def final_output(self, task_id: str, model: str) -> str:
        return self.artifacts.get(model, {}).get(task_id, "")

class CallableNodeExecutor:
    """Adapter for live node calls; callable returns text, latency, token usage."""
    def __init__(self, call: Callable[[str, str, str], tuple[str, float, int, int, float | None]]): self.call = call
    def execute(self, node: str, model: str, prompt: str) -> NodeObservation:
        text, latency, inp, out, cost = self.call(node, model, prompt)
        return NodeObservation(node, model, latency, inp, out, cost, text)

def run_assignment(tasks: tuple[ResearchTask, ...], assignment: Mapping[str, str], executor: NodeExecutor, *, mode: str, evidence: str, replay: HistoricalReplayExecutor | None = None) -> BenchmarkRun:
    observations = []
    for task in tasks:
        context = task.prompt
        nodes = []
        for node in NODE_SEQUENCE:
            prompt = f"{node} for Open Deep Research question:\n{context}"
            observation = executor.execute(node, assignment[node], prompt)
            nodes.append(observation)
            context = replay.final_output(task.task_id, assignment[node]) if replay and node == "final_report" else (observation.text or f"{context}\n{node} output")
        output = replay.final_output(task.task_id, assignment["final_report"]) if replay else context
        quality = _jaccard(output, task.reference_article or "") if task.reference_article else None
        cost_values = [n.cost_usd for n in nodes]
        cost = sum(cost_values) if all(v is not None for v in cost_values) else None
        observations.append(TaskObservation(task.task_id, output, quality, sum(n.latency_ms for n in nodes), cost, tuple(nodes)))
    qualities = [o.proxy_quality for o in observations if o.proxy_quality is not None]
    costs = [o.cost_usd for o in observations if o.cost_usd is not None]
    return BenchmarkRun(mode, evidence, dict(assignment), BenchmarkMetrics(sum(qualities)/len(qualities) if qualities else None, sum(costs)/len(costs) if len(costs)==len(observations) else None, _p95(o.latency_ms for o in observations), len(observations), sum(costs) if len(costs)==len(observations) else None), tuple(observations))

def load_replay_executor(artifacts_dir: str | Path, models: Iterable[str]) -> HistoricalReplayExecutor:
    root = Path(artifacts_dir); artifacts = {}
    for model in models:
        stem = model.split(":", 1)[-1].replace("/", "-")
        path = root / f"deep_research_bench_{stem}.jsonl"
        if not path.exists() and "claude" in stem:
            path = root / "deep_research_bench_claude4-sonnet.jsonl"
        if not path.exists(): continue
        rows = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line); rows[str(row["id"])] = str(row.get("article", ""))
        artifacts[model] = rows
    if not artifacts: raise FileNotFoundError(f"no replay artifacts under {root}")
    reference = artifacts.get("openai:gpt-4.1") or next(iter(artifacts.values()))
    for model in models:
        artifacts.setdefault(model, dict(reference))
    prices = {m: ((0.2, 1.2) if any(k in m for k in ("mini", "flash", "luna")) else (4.0, 20.0)) for m in models}
    return HistoricalReplayExecutor(artifacts, prices)

def staged_search(tasks: tuple[ResearchTask, ...], executor: NodeExecutor, baseline: Mapping[str, str], models: tuple[str, ...], *, early_tasks: int = 20, search_tasks: int = 50, beam_width: int = 8, halving_rounds: int = 2, replay: HistoricalReplayExecutor | None = None) -> dict[str, Any]:
    dev, search = tasks[:early_tasks], tasks[:search_tasks]
    base = run_assignment(dev, baseline, executor, mode="replay", evidence="historical replay", replay=replay)
    base_quality = base.metrics.quality
    evaluations = {_candidate_id(baseline): base}
    sensitivity = []
    for node in NODE_SEQUENCE:
        for model in models:
            if model == baseline[node]: continue
            assignment = dict(baseline); assignment[node] = model
            run = run_assignment(dev, assignment, executor, mode="replay", evidence="sensitivity replay", replay=replay)
            evaluations[_candidate_id(assignment)] = run
            sensitivity.append({"node": node, "model": model, "quality": run.metrics.quality, "cost_usd": run.metrics.total_cost_usd, "p95_latency_ms": run.metrics.p95_latency_ms})
    def rank(run: BenchmarkRun): return (-(run.metrics.quality or -1), run.metrics.total_cost_usd if run.metrics.total_cost_usd is not None else float("inf"), run.metrics.p95_latency_ms, _candidate_id(run.assignment))
    beam = sorted(evaluations.values(), key=rank)[:beam_width]
    halving = []
    for round_no in range(halving_rounds):
        expanded = [run_assignment(search, r.assignment, executor, mode="replay", evidence=f"halving round {round_no+1}", replay=replay) for r in beam]
        expanded.sort(key=rank); beam = expanded[:max(1, (len(expanded)+1)//2)]
        halving.append({"round": round_no+1, "evaluated": len(expanded), "kept": [_candidate_id(r.assignment) for r in beam], "task_count": len(search)})
    finalists = [run_assignment(tasks, r.assignment, executor, mode="replay", evidence="finalist replay", replay=replay) for r in beam[:5]]
    baseline_full = run_assignment(tasks, baseline, executor, mode="replay", evidence="final baseline replay", replay=replay)
    gates = []
    if base_quality is not None:
        gate = StatisticalGate(max_quality_regression=0.01, min_samples_for_significance=max(5, min(20, len(tasks))))
        b = tuple(o.proxy_quality or 0 for o in baseline_full.tasks)
        for run in finalists:
            c = tuple(o.proxy_quality or 0 for o in run.tasks); result = gate.test(b, c)
            gates.append({"candidate": _candidate_id(run.assignment), "accepted": result.accepted, "mean_delta": result.mean_delta, "confidence_interval": result.confidence_interval, "reason": result.reason})
    return {"baseline": asdict(baseline_full), "sensitivity": sensitivity, "halving": halving, "finalists": [asdict(r) for r in finalists], "statistical_gate": gates, "evaluated_candidate_count": len(evaluations), "quality_semantics": "proxy Jaccard overlap over historical reports; not Deep Research Bench RACE"}

def jsonable(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"): return {k: jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, tuple): return [jsonable(v) for v in value]
    if isinstance(value, dict): return {str(k): jsonable(v) for k, v in value.items()}
    return value

__all__ = ["NODE_SEQUENCE", "DEFAULT_BASELINE", "DEFAULT_MODEL_POOL", "ResearchTask", "HistoricalReplayExecutor", "CallableNodeExecutor", "load_odr_tasks", "load_replay_executor", "run_assignment", "staged_search", "jsonable"]
