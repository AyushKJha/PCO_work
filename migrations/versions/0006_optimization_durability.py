"""Persist optimization request metadata, idempotency, and replayable events.

Revision ID: 0006_optimization_durability
"""

from alembic import op
import sqlalchemy as sa


revision = "0006_optimization_durability"
# 0005 currently has two additive migration branches (auth and eval runs).
down_revision = ("0005_eval_runs", "0005_user_auth")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Batch mode keeps this migration runnable on SQLite for local CI while
    # emitting ordinary ALTER TABLE operations on PostgreSQL.
    with op.batch_alter_table("jobs", recreate="auto") as batch:
        batch.add_column(sa.Column("project_version_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("dataset_id", sa.String(length=36), nullable=True))
        batch.add_column(sa.Column("idempotency_key", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("objective", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("quality_tolerance_pp", sa.Numeric(9, 4), nullable=True))
        batch.add_column(sa.Column("confidence_pct", sa.Numeric(9, 4), nullable=True))
        batch.add_column(sa.Column("allowed_models", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key("fk_jobs_project_version_id", "project_versions", ["project_version_id"], ["id"], ondelete="SET NULL")
        batch.create_foreign_key("fk_jobs_dataset_id", "eval_datasets", ["dataset_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_jobs_project_version_id", "jobs", ["project_version_id"])
    op.create_index("ix_jobs_dataset_id", "jobs", ["dataset_id"])
    op.create_index("ix_jobs_idempotency_key", "jobs", ["idempotency_key"])

    op.create_table(
        "optimization_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False, server_default="INFO"),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("job_id", "sequence", name="uq_optimization_events_job_sequence"),
        sa.UniqueConstraint("job_id", "event_id", name="uq_optimization_events_job_event"),
    )
    op.create_index("ix_optimization_events_organization_id", "optimization_events", ["organization_id"])
    op.create_index("ix_optimization_events_project_id", "optimization_events", ["project_id"])
    op.create_index("ix_optimization_events_job_id", "optimization_events", ["job_id"])
    op.create_index("ix_optimization_events_job_created", "optimization_events", ["job_id", "created_at"])

    op.create_table(
        "optimization_idempotency",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("organization_id", "operation", "idempotency_key", name="uq_optimization_idempotency_key"),
        sa.UniqueConstraint("job_id"),
    )
    op.create_index("ix_optimization_idempotency_organization_id", "optimization_idempotency", ["organization_id"])


def downgrade() -> None:
    op.drop_index("ix_optimization_idempotency_organization_id", table_name="optimization_idempotency")
    op.drop_table("optimization_idempotency")
    op.drop_index("ix_optimization_events_job_created", table_name="optimization_events")
    op.drop_index("ix_optimization_events_job_id", table_name="optimization_events")
    op.drop_index("ix_optimization_events_project_id", table_name="optimization_events")
    op.drop_index("ix_optimization_events_organization_id", table_name="optimization_events")
    op.drop_table("optimization_events")
    op.drop_index("ix_jobs_idempotency_key", table_name="jobs")
    op.drop_index("ix_jobs_dataset_id", table_name="jobs")
    op.drop_index("ix_jobs_project_version_id", table_name="jobs")
    with op.batch_alter_table("jobs", recreate="auto") as batch:
        batch.drop_constraint("fk_jobs_dataset_id", type_="foreignkey")
        batch.drop_constraint("fk_jobs_project_version_id", type_="foreignkey")
        for name in ("cancel_requested_at", "allowed_models", "confidence_pct", "quality_tolerance_pp", "objective", "idempotency_key", "dataset_id", "project_version_id"):
            batch.drop_column(name)
