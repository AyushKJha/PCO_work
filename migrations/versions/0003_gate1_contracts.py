"""Persist Gate 1 evaluation inputs, outbox events, and result metadata.

Revision ID: 0003_gate1_contracts
"""
from alembic import op
import sqlalchemy as sa


revision = "0003_gate1_contracts"
down_revision = "0002_worker_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eval_datasets",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("organization_id", "name", "version", name="uq_eval_datasets_organization_name_version"),
    )
    op.create_index("ix_eval_datasets_organization_id", "eval_datasets", ["organization_id"])
    op.create_index("ix_eval_datasets_project_id", "eval_datasets", ["project_id"])
    op.create_index("ix_eval_datasets_organization_created", "eval_datasets", ["organization_id", "created_at"])
    op.create_table(
        "eval_cases",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("case_id", sa.String(length=255), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("expected", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["eval_datasets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("dataset_id", "case_id", name="uq_eval_cases_dataset_case"),
    )
    op.create_index("ix_eval_cases_dataset_id", "eval_cases", ["dataset_id"])
    op.create_index("ix_eval_cases_dataset_ordinal", "eval_cases", ["dataset_id", "ordinal"])
    op.create_table(
        "eval_graders",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["eval_datasets.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("dataset_id", "name", name="uq_eval_graders_dataset_name"),
    )
    op.create_index("ix_eval_graders_dataset_id", "eval_graders", ["dataset_id"])
    op.create_index("ix_eval_graders_dataset_ordinal", "eval_graders", ["dataset_id", "ordinal"])
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), nullable=True),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index("ix_outbox_events_organization_id", "outbox_events", ["organization_id"])
    op.create_index("ix_outbox_events_status", "outbox_events", ["status"])
    op.create_index("ix_outbox_events_claimable", "outbox_events", ["status", "available_at", "created_at"])
    op.create_index("ix_outbox_events_aggregate", "outbox_events", ["aggregate_type", "aggregate_id"])
    op.create_table(
        "optimization_results",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("schema_version", sa.String(length=16), nullable=False, server_default="v1"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("recommendation", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("job_id"),
    )
    op.create_index("ix_optimization_results_organization_id", "optimization_results", ["organization_id"])
    op.create_index("ix_optimization_results_project_id", "optimization_results", ["project_id"])
    op.create_index("ix_optimization_results_status", "optimization_results", ["status"])
    op.create_index("ix_optimization_results_organization_created", "optimization_results", ["organization_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_optimization_results_organization_created", table_name="optimization_results")
    op.drop_index("ix_optimization_results_status", table_name="optimization_results")
    op.drop_index("ix_optimization_results_project_id", table_name="optimization_results")
    op.drop_index("ix_optimization_results_organization_id", table_name="optimization_results")
    op.drop_table("optimization_results")
    op.drop_index("ix_outbox_events_aggregate", table_name="outbox_events")
    op.drop_index("ix_outbox_events_claimable", table_name="outbox_events")
    op.drop_index("ix_outbox_events_status", table_name="outbox_events")
    op.drop_index("ix_outbox_events_organization_id", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_eval_graders_dataset_ordinal", table_name="eval_graders")
    op.drop_index("ix_eval_graders_dataset_id", table_name="eval_graders")
    op.drop_table("eval_graders")
    op.drop_index("ix_eval_cases_dataset_ordinal", table_name="eval_cases")
    op.drop_index("ix_eval_cases_dataset_id", table_name="eval_cases")
    op.drop_table("eval_cases")
    op.drop_index("ix_eval_datasets_organization_created", table_name="eval_datasets")
    op.drop_index("ix_eval_datasets_project_id", table_name="eval_datasets")
    op.drop_index("ix_eval_datasets_organization_id", table_name="eval_datasets")
    op.drop_table("eval_datasets")
