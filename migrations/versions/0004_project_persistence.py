"""Persist project graph versions, settings, and UI layout revisions.

Revision ID: 0004_project_persistence
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_project_persistence"
down_revision = "0003_gate1_contracts"
branch_labels = None
depends_on = None


def _indexes(table: str, columns: list[str], prefix: str) -> None:
    for column in columns:
        op.create_index(f"ix_{prefix}_{column}", table, [column])


def upgrade() -> None:
    op.create_table(
        "project_versions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("environment", sa.String(length=32), nullable=False, server_default="STAGING"),
        sa.Column("run_id", sa.String(length=255), nullable=True),
        sa.Column("total_executions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("baseline_cost", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("optimized_cost", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("savings_pct", sa.Numeric(9, 4), nullable=False, server_default="0"),
        sa.Column("monthly_savings_estimate", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("monthly_requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("baseline_latency_p95", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("optimized_latency_p95", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("baseline_quality", sa.Numeric(9, 4), nullable=False, server_default="0"),
        sa.Column("optimized_quality", sa.Numeric(9, 4), nullable=False, server_default="0"),
        sa.Column("eval_cases_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quality_tolerance_pct", sa.Numeric(9, 4), nullable=False, server_default="1"),
        sa.Column("confidence_pct", sa.Numeric(9, 4), nullable=False, server_default="95"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "version", name="uq_project_versions_project_version"),
    )
    _indexes("project_versions", ["organization_id", "project_id"], "project_versions")
    op.create_index(
        "ix_project_versions_organization_created", "project_versions", ["organization_id", "created_at"]
    )

    op.create_table(
        "agent_nodes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.Column("node_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("x", sa.Float(), nullable=False, server_default="0"),
        sa.Column("y", sa.Float(), nullable=False, server_default="0"),
        sa.Column("baseline_model", sa.String(length=255), nullable=False),
        sa.Column("current_model", sa.String(length=255), nullable=False),
        sa.Column("optimized_model", sa.String(length=255), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_cost", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("baseline_cost", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("optimized_cost", sa.Numeric(18, 6), nullable=False, server_default="0"),
        sa.Column("latency_sec", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("baseline_latency_sec", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("optimized_latency_sec", sa.Numeric(12, 4), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_share_pct", sa.Numeric(9, 4), nullable=False, server_default="0"),
        sa.Column("quality_sensitivity", sa.String(length=16), nullable=False, server_default="MEDIUM"),
        sa.Column("is_hotspot", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("prompt_template", sa.Text(), nullable=True),
        sa.Column("candidates", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["project_versions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("version_id", "node_id", name="uq_agent_nodes_version_node"),
    )
    _indexes("agent_nodes", ["organization_id", "project_id", "version_id"], "agent_nodes")

    op.create_table(
        "graph_edges",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("version_id", sa.String(length=36), nullable=False),
        sa.Column("edge_id", sa.String(length=255), nullable=False),
        sa.Column("from_node", sa.String(length=255), nullable=False),
        sa.Column("to_node", sa.String(length=255), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=True),
        sa.Column("throughput_tokens_per_sec", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("avg_latency_ms", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["project_versions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("version_id", "edge_id", name="uq_graph_edges_version_edge"),
    )
    _indexes("graph_edges", ["organization_id", "project_id", "version_id"], "graph_edges")

    op.create_table(
        "project_settings",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("quality_tolerance_pct", sa.Numeric(9, 4), nullable=False, server_default="1"),
        sa.Column("confidence_pct", sa.Numeric(9, 4), nullable=False, server_default="95"),
        sa.Column("max_p95_latency_ms", sa.Integer(), nullable=True),
        sa.Column("objective", sa.JSON(), nullable=False, server_default='{"minimize":["cost","latency"]}'),
        sa.Column("allowed_models", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id"),
    )
    _indexes("project_settings", ["organization_id", "project_id"], "project_settings")

    op.create_table(
        "project_layouts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("version_id", sa.String(length=36), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("nodes", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["version_id"], ["project_versions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("project_id", "revision", name="uq_project_layouts_project_revision"),
    )
    _indexes("project_layouts", ["organization_id", "project_id", "version_id"], "project_layouts")
    op.create_index("ix_project_layouts_project_updated", "project_layouts", ["project_id", "updated_at"])


def downgrade() -> None:
    op.drop_index("ix_project_layouts_project_updated", table_name="project_layouts")
    for index in ("version_id", "project_id", "organization_id"):
        op.drop_index(f"ix_project_layouts_{index}", table_name="project_layouts")
    op.drop_table("project_layouts")
    for index in ("project_id", "organization_id"):
        op.drop_index(f"ix_project_settings_{index}", table_name="project_settings")
    op.drop_table("project_settings")
    for index in ("version_id", "project_id", "organization_id"):
        op.drop_index(f"ix_graph_edges_{index}", table_name="graph_edges")
    op.drop_table("graph_edges")
    for index in ("version_id", "project_id", "organization_id"):
        op.drop_index(f"ix_agent_nodes_{index}", table_name="agent_nodes")
    op.drop_table("agent_nodes")
    op.drop_index("ix_project_versions_organization_created", table_name="project_versions")
    for index in ("project_id", "organization_id"):
        op.drop_index(f"ix_project_versions_{index}", table_name="project_versions")
    op.drop_table("project_versions")
