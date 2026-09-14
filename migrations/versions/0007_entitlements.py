"""Persist manual Free/Pro organization entitlement state.

Revision ID: 0007_entitlements
"""

from alembic import op
import sqlalchemy as sa


revision = "0007_entitlements"
down_revision = "0006_optimization_durability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("plan", sa.String(length=16), nullable=False, server_default="free"))
    op.add_column("organizations", sa.Column("plan_status", sa.String(length=16), nullable=False, server_default="active"))
    op.add_column("organizations", sa.Column("plan_source", sa.String(length=32), nullable=False, server_default="manual"))
    op.add_column("organizations", sa.Column("plan_expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "plan_expires_at")
    op.drop_column("organizations", "plan_source")
    op.drop_column("organizations", "plan_status")
    op.drop_column("organizations", "plan")
