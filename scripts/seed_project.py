"""Idempotently seed the database with the local TwineRun research project.

This is a database fixture for local/staging browser testing. It is deliberately
separate from the frontend so the browser can only consume persisted records.
"""

from __future__ import annotations

import argparse
import os
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from apps.api.db import create_session_factory, create_tables
from apps.api.models import AgentNode, GraphEdge, Organization, Project, ProjectSettings, ProjectVersion
from apps.api.project_serializers import validate_graph


NODES: tuple[dict[str, Any], ...] = (
    {"id": "node-planner", "name": "Planner", "role": "planner", "x": 180, "y": 194, "model": "openai/gpt-5.6-sol"},
    {"id": "node-researcher", "name": "Researcher", "role": "researcher", "x": 430, "y": 194, "model": "openai/gpt-5.6-sol"},
    {"id": "node-extractor", "name": "Extractor", "role": "extractor", "x": 680, "y": 194, "model": "openai/gpt-5.6-sol"},
    {"id": "node-reasoner", "name": "Reasoner", "role": "reasoner", "x": 930, "y": 194, "model": "openai/gpt-5.6-sol"},
    {"id": "node-formatter", "name": "Formatter", "role": "formatter", "x": 1180, "y": 194, "model": "openai/gpt-5.6-sol"},
)
EDGES: tuple[tuple[str, str], ...] = tuple(zip((node["id"] for node in NODES), (node["id"] for node in NODES[1:])))


def seed_research_project(session_factory: sessionmaker[Session], *, organization_id: str | None = None) -> dict[str, str]:
    """Create the fixture once and return stable IDs on every invocation."""
    with session_factory.begin() as session:
        organization = session.get(Organization, organization_id) if organization_id else session.scalar(
            select(Organization).where(Organization.name == "TwineRun Demo")
        )
        if organization is None:
            organization = Organization(id=organization_id, name="TwineRun Demo") if organization_id else Organization(name="TwineRun Demo")
            session.add(organization)
            session.flush()
        project = session.scalar(select(Project).where(Project.organization_id == organization.id, Project.slug == "research-agent"))
        if project is None:
            project = Project(organization_id=organization.id, name="Research Agent", slug="research-agent")
            session.add(project)
            session.flush()
        version = session.scalar(select(ProjectVersion).where(ProjectVersion.project_id == project.id, ProjectVersion.version == "v1"))
        if version is None:
            validate_graph(NODES, [{"from": source, "to": target} for source, target in EDGES])
            version = ProjectVersion(
                organization_id=organization.id, project_id=project.id, version="v1", environment="STAGING",
                total_executions=0, baseline_cost=0, optimized_cost=0, savings_pct=0,
                monthly_savings_estimate=0, monthly_requests=0, baseline_latency_p95=0,
                optimized_latency_p95=0, baseline_quality=0, optimized_quality=0,
                eval_cases_count=0, quality_tolerance_pct=1, confidence_pct=95,
            )
            version.nodes = [AgentNode(
                organization_id=organization.id, project_id=project.id, node_id=node["id"],
                name=node["name"], role=node["role"], x=node["x"], y=node["y"],
                baseline_model=node["model"], current_model=node["model"], optimized_model=node["model"],
                quality_sensitivity="MEDIUM", is_hotspot=False, candidates=[], ordinal=index,
            ) for index, node in enumerate(NODES)]
            version.edges = [GraphEdge(
                organization_id=organization.id, project_id=project.id, edge_id=f"edge-{index}",
                from_node=source, to_node=target, ordinal=index,
            ) for index, (source, target) in enumerate(EDGES)]
            session.add(version)
        settings = session.scalar(select(ProjectSettings).where(ProjectSettings.project_id == project.id))
        if settings is None:
            session.add(ProjectSettings(organization_id=organization.id, project_id=project.id))
        session.flush()
        return {"organization_id": organization.id, "project_id": project.id, "version_id": version.id}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--organization-id", default=os.getenv("SEED_ORGANIZATION_ID"))
    args = parser.parse_args()
    factory = create_session_factory(args.database_url)
    create_tables(factory)
    print(seed_research_project(factory, organization_id=args.organization_id))


if __name__ == "__main__":
    main()
