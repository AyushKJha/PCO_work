"""Persist Dodo checkout sessions, webhook receipts, and provider IDs.

Revision ID: 0008_dodo_billing
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_dodo_billing"
down_revision = "0007_entitlements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("dodo_customer_id", sa.String(length=255), nullable=True))
    op.add_column("organizations", sa.Column("dodo_subscription_id", sa.String(length=255), nullable=True))
    op.add_column("organizations", sa.Column("dodo_subscription_status", sa.String(length=32), nullable=True))
    op.create_index("ix_organizations_dodo_customer_id", "organizations", ["dodo_customer_id"])
    op.create_unique_constraint("uq_organizations_dodo_subscription_id", "organizations", ["dodo_subscription_id"])

    op.create_table(
        "billing_checkouts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("plan", sa.String(length=16), nullable=False, server_default="pro"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("provider_session_id", sa.String(length=255), nullable=True),
        sa.Column("checkout_url", sa.String(length=2048), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("organization_id", "idempotency_key", name="uq_billing_checkouts_org_idempotency"),
        sa.UniqueConstraint("provider_session_id", name="uq_billing_checkouts_provider_session_id"),
    )
    op.create_index("ix_billing_checkouts_organization_id", "billing_checkouts", ["organization_id"])
    op.create_index("ix_billing_checkouts_user_id", "billing_checkouts", ["user_id"])
    op.create_index("ix_billing_checkouts_status", "billing_checkouts", ["status"])
    op.create_index("ix_billing_checkouts_provider_session", "billing_checkouts", ["provider_session_id"])
    op.create_index("ix_billing_checkouts_organization_created", "billing_checkouts", ["organization_id", "created_at"])

    op.create_table(
        "billing_webhook_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("provider_event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="received"),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("provider_event_id", name="uq_billing_webhook_events_provider_event_id"),
    )
    op.create_index("ix_billing_webhook_events_provider_event_id", "billing_webhook_events", ["provider_event_id"])
    op.create_index("ix_billing_webhook_events_event_type", "billing_webhook_events", ["event_type"])
    op.create_index("ix_billing_webhook_events_status", "billing_webhook_events", ["status"])


def downgrade() -> None:
    op.drop_index("ix_billing_webhook_events_status", table_name="billing_webhook_events")
    op.drop_index("ix_billing_webhook_events_event_type", table_name="billing_webhook_events")
    op.drop_index("ix_billing_webhook_events_provider_event_id", table_name="billing_webhook_events")
    op.drop_table("billing_webhook_events")
    op.drop_index("ix_billing_checkouts_organization_created", table_name="billing_checkouts")
    op.drop_index("ix_billing_checkouts_provider_session", table_name="billing_checkouts")
    op.drop_index("ix_billing_checkouts_status", table_name="billing_checkouts")
    op.drop_index("ix_billing_checkouts_user_id", table_name="billing_checkouts")
    op.drop_index("ix_billing_checkouts_organization_id", table_name="billing_checkouts")
    op.drop_table("billing_checkouts")
    op.drop_constraint("uq_organizations_dodo_subscription_id", "organizations", type_="unique")
    op.drop_index("ix_organizations_dodo_customer_id", table_name="organizations")
    op.drop_column("organizations", "dodo_subscription_status")
    op.drop_column("organizations", "dodo_subscription_id")
    op.drop_column("organizations", "dodo_customer_id")
