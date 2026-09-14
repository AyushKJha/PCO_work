"""Persist canonical evaluation suite runs and per-case evidence.

Revision ID: 0005_eval_runs
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_eval_runs"
down_revision = "0004_project_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("eval_suite_id", sa.String(length=36), nullable=False),
        sa.Column("project_version_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("candidate_config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("grader_snapshot", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("aggregate_metrics", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["eval_suite_id"], ["eval_datasets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_version_id"], ["project_versions.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_eval_runs_organization_id", "eval_runs", ["organization_id"])
    op.create_index("ix_eval_runs_project_id", "eval_runs", ["project_id"])
    op.create_index("ix_eval_runs_eval_suite_id", "eval_runs", ["eval_suite_id"])
    op.create_index("ix_eval_runs_project_created", "eval_runs", ["project_id", "created_at"])
    op.create_index("ix_eval_runs_suite_created", "eval_runs", ["eval_suite_id", "created_at"])
    op.create_index("ix_eval_runs_organization_created", "eval_runs", ["organization_id", "created_at"])

    op.create_table(
        "eval_run_cases",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("eval_run_id", sa.String(length=36), nullable=False),
        sa.Column("case_id", sa.String(length=255), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["eval_run_id"], ["eval_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("eval_run_id", "case_id", name="uq_eval_run_cases_run_case"),
    )
    op.create_index("ix_eval_run_cases_eval_run_id", "eval_run_cases", ["eval_run_id"])
    op.create_index("ix_eval_run_cases_run_ordinal", "eval_run_cases", ["eval_run_id", "ordinal"])


def downgrade() -> None:
    op.drop_index("ix_eval_run_cases_run_ordinal", table_name="eval_run_cases")
    op.drop_index("ix_eval_run_cases_eval_run_id", table_name="eval_run_cases")
    op.drop_table("eval_run_cases")
    op.drop_index("ix_eval_runs_organization_created", table_name="eval_runs")
    op.drop_index("ix_eval_runs_suite_created", table_name="eval_runs")
    op.drop_index("ix_eval_runs_project_created", table_name="eval_runs")
    op.drop_index("ix_eval_runs_eval_suite_id", table_name="eval_runs")
    op.drop_index("ix_eval_runs_project_id", table_name="eval_runs")
    op.drop_index("ix_eval_runs_organization_id", table_name="eval_runs")
    op.drop_table("eval_runs")
