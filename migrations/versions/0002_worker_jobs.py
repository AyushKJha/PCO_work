"""Add durable worker jobs and idempotent candidate results.

Revision ID: 0002_worker_jobs
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_worker_jobs"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("checkpoint", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("result", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("max_experiment_cost_usd", sa.Float(), nullable=False, server_default="25"),
        sa.Column("spent_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_by", sa.String(length=255), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_jobs_organization_id", "jobs", ["organization_id"])
    op.create_index("ix_jobs_project_id", "jobs", ["project_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_claimable", "jobs", ["status", "available_at", "claim_expires_at"])
    op.create_index("ix_jobs_organization_created", "jobs", ["organization_id", "created_at"])
    op.create_table(
        "job_candidate_results",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="completed"),
        sa.Column("result", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("job_id", "candidate_id", name="uq_job_candidate_results_job_candidate"),
    )
    op.create_index("ix_job_candidate_results_job_id", "job_candidate_results", ["job_id"])


def downgrade() -> None:
    op.drop_index("ix_job_candidate_results_job_id", table_name="job_candidate_results")
    op.drop_table("job_candidate_results")
    op.drop_index("ix_jobs_organization_created", table_name="jobs")
    op.drop_index("ix_jobs_claimable", table_name="jobs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_index("ix_jobs_project_id", table_name="jobs")
    op.drop_index("ix_jobs_organization_id", table_name="jobs")
    op.drop_table("jobs")
