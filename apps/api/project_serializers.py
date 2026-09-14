"""Validation and wire serializers for persisted TwineRun project data."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal
from typing import Any


class GraphValidationError(ValueError):
    """Raised when a version graph cannot be executed safely."""


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def validate_graph(nodes: Iterable[Any], edges: Iterable[Any]) -> None:
    """Reject duplicate node IDs, dangling edges, and directed cycles."""
    node_ids: list[str] = []
    for node in nodes:
        node_id = str(_field(node, "node_id", _field(node, "id", ""))).strip()
        if not node_id:
            raise GraphValidationError("Node id is required")
        if node_id in node_ids:
            raise GraphValidationError(f"Duplicate node id: {node_id}")
        node_ids.append(node_id)

    known = set(node_ids)
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    indegree = {node_id: 0 for node_id in node_ids}
    seen_edges: set[tuple[str, str]] = set()
    for edge in edges:
        source = str(_field(edge, "from_node", _field(edge, "fromNode", _field(edge, "from", "")))).strip()
        target = str(_field(edge, "to_node", _field(edge, "toNode", _field(edge, "to", "")))).strip()
        if source not in known or target not in known:
            raise GraphValidationError(f"Dangling edge: {source} -> {target}")
        pair = (source, target)
        if pair not in seen_edges:
            seen_edges.add(pair)
            adjacency[source].add(target)
            indegree[target] += 1

    queue = [node_id for node_id in node_ids if indegree[node_id] == 0]
    visited = 0
    while queue:
        current = queue.pop(0)
        visited += 1
        for target in adjacency[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if visited != len(node_ids):
        raise GraphValidationError("Graph contains a cycle")


def _number(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def serialize_substitution(item: Any) -> dict[str, Any]:
    return {
        "model": str(_field(item, "model", "")),
        "costDelta": _number(_field(item, "costDelta", _field(item, "cost_delta", 0))),
        "costDeltaPct": _number(_field(item, "costDeltaPct", _field(item, "cost_delta_pct", 0))),
        "qualityDelta": _number(_field(item, "qualityDelta", _field(item, "quality_delta", 0))),
        "latencyDeltaSec": _number(_field(item, "latencyDeltaSec", _field(item, "latency_delta_sec", 0))),
        "status": str(_field(item, "status", "VIABLE")).upper(),
        "reason": str(_field(item, "reason", "Measured against the stored baseline.")),
    }


def serialize_node(node: Any) -> dict[str, Any]:
    # Prompt templates are deliberately omitted unless a future explicit
    # content-retention policy asks for them. Metadata remains useful without
    # exposing customer prompts to the browser.
    return {
        "id": str(node.node_id),
        "name": node.name,
        "role": node.role,
        "x": _number(node.x),
        "y": _number(node.y),
        "baselineModel": node.baseline_model,
        "currentModel": node.current_model,
        "optimizedModel": node.optimized_model,
        "calls": _int(node.calls),
        "avgCost": _number(node.avg_cost),
        "baselineCost": _number(node.baseline_cost),
        "optimizedCost": _number(node.optimized_cost),
        "latencySec": _number(node.latency_sec),
        "baselineLatencySec": _number(node.baseline_latency_sec),
        "optimizedLatencySec": _number(node.optimized_latency_sec),
        "inputTokens": _int(node.input_tokens),
        "outputTokens": _int(node.output_tokens),
        "costSharePct": _number(node.cost_share_pct),
        "qualitySensitivity": str(node.quality_sensitivity).upper(),
        "isHotspot": bool(node.is_hotspot),
        "promptTemplate": None,
        "candidates": [serialize_substitution(item) for item in (node.candidates or [])],
    }


def serialize_edge(edge: Any) -> dict[str, Any]:
    return {
        "id": str(edge.edge_id),
        "from": edge.from_node,
        "to": edge.to_node,
        "label": edge.label,
        "throughputTokensPerSec": _number(edge.throughput_tokens_per_sec),
        "avgLatencyMs": _number(edge.avg_latency_ms),
    }


def serialize_version(version: Any) -> dict[str, Any]:
    validate_graph(version.nodes, version.edges)
    return {
        "id": str(version.id),
        "projectId": str(version.project_id),
        "version": version.version,
        "environment": version.environment,
        "runId": str(version.run_id) if version.run_id else "",
        "totalExecutions": _int(version.total_executions),
        "baselineCost": _number(version.baseline_cost),
        "optimizedCost": _number(version.optimized_cost),
        "savingsPct": _number(version.savings_pct),
        "monthlySavingsEstimate": _number(version.monthly_savings_estimate),
        "monthlyRequests": _int(version.monthly_requests),
        "baselineLatencyP95": _number(version.baseline_latency_p95),
        "optimizedLatencyP95": _number(version.optimized_latency_p95),
        "baselineQuality": _number(version.baseline_quality),
        "optimizedQuality": _number(version.optimized_quality),
        "evalCasesCount": _int(version.eval_cases_count),
        "qualityTolerancePct": _number(version.quality_tolerance_pct),
        "confidencePct": _number(version.confidence_pct),
        "nodes": [serialize_node(node) for node in version.nodes],
        "edges": [serialize_edge(edge) for edge in version.edges],
    }


def serialize_project(project: Any, version: Any | None) -> dict[str, Any]:
    payload = serialize_version(version) if version is not None else {
        "id": "",
        "projectId": str(project.id),
        "version": "",
        "environment": "STAGING",
        "runId": "",
        "totalExecutions": 0,
        "baselineCost": 0.0,
        "optimizedCost": 0.0,
        "savingsPct": 0.0,
        "monthlySavingsEstimate": 0.0,
        "monthlyRequests": 0,
        "baselineLatencyP95": 0.0,
        "optimizedLatencyP95": 0.0,
        "baselineQuality": 0.0,
        "optimizedQuality": 0.0,
        "evalCasesCount": 0,
        "qualityTolerancePct": 1.0,
        "confidencePct": 95.0,
        "nodes": [],
        "edges": [],
    }
    payload.update({"id": str(project.id), "name": project.name})
    return payload


def serialize_settings(settings: Any) -> dict[str, Any]:
    return {
        "projectId": str(settings.project_id),
        "qualityTolerancePp": _number(settings.quality_tolerance_pct, 1.0),
        "qualityTolerancePct": _number(settings.quality_tolerance_pct, 1.0),
        "confidencePct": _number(settings.confidence_pct, 95.0),
        "maxP95LatencyMs": settings.max_p95_latency_ms,
        "objective": settings.objective or {"minimize": ["cost", "latency"]},
        "allowedModels": list(settings.allowed_models or []),
        "updatedAt": settings.updated_at.isoformat() if settings.updated_at else None,
    }


def serialize_layout(layout: Any | None, project_id: str) -> dict[str, Any]:
    return {
        "projectId": project_id,
        "versionId": str(layout.version_id) if layout and layout.version_id else None,
        "revision": int(layout.revision) if layout else 0,
        "nodes": dict(layout.nodes or {}) if layout else {},
        "updatedAt": layout.updated_at.isoformat() if layout and layout.updated_at else None,
    }
