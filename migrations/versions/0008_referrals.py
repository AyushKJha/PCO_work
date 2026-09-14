"""Persist early-Pro referral attribution and reward ledger.

Revision ID: 0008_referrals
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_referrals"
# Dodo billing is the other launch migration on the shared branch.  Referral
# tables depend on its organization provider identifiers and therefore form a
# linear migration after it rather than creating a second Alembic head.
down_revision = "0008_dodo_billing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "referral_codes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("organization_id", name="uq_referral_codes_organization"),
        sa.UniqueConstraint("code", name="uq_referral_codes_code"),
    )
    op.create_index("ix_referral_codes_organization_id", "referral_codes", ["organization_id"])
    op.create_index("ix_referral_codes_created_by_user_id", "referral_codes", ["created_by_user_id"])
    op.create_index("ix_referral_codes_code", "referral_codes", ["code"])
    op.create_index("ix_referral_codes_active", "referral_codes", ["code", "active"])

    op.create_table(
        "referrals",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("referral_code_id", sa.String(length=36), nullable=False),
        sa.Column("referrer_user_id", sa.String(length=36), nullable=False),
        sa.Column("referrer_organization_id", sa.String(length=36), nullable=False),
        sa.Column("invitee_user_id", sa.String(length=36), nullable=False),
        sa.Column("invitee_organization_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("subscription_id", sa.String(length=255), nullable=True),
        sa.Column("attribution_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("qualified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["referral_code_id"], ["referral_codes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["referrer_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["referrer_organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invitee_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invitee_organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("invitee_organization_id", name="uq_referrals_invitee_organization"),
    )
    for name, columns in (
        ("ix_referrals_referral_code_id", ["referral_code_id"]),
        ("ix_referrals_referrer_user_id", ["referrer_user_id"]),
        ("ix_referrals_referrer_organization_id", ["referrer_organization_id"]),
        ("ix_referrals_invitee_user_id", ["invitee_user_id"]),
        ("ix_referrals_invitee_organization_id", ["invitee_organization_id"]),
        ("ix_referrals_subscription_id", ["subscription_id"]),
        ("ix_referrals_status", ["status"]),
        ("ix_referrals_referrer_status", ["referrer_organization_id", "status"]),
        ("ix_referrals_invitee_status", ["invitee_organization_id", "status"]),
    ):
        op.create_index(name, "referrals", columns)

    op.create_table(
        "referral_rewards",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("referral_id", sa.String(length=36), nullable=False),
        sa.Column("recipient_user_id", sa.String(length=36), nullable=False),
        sa.Column("recipient_organization_id", sa.String(length=36), nullable=False),
        sa.Column("reward_type", sa.String(length=32), nullable=False, server_default="FREE_PRO_MONTH"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("provider_reference", sa.String(length=255), nullable=True),
        sa.Column("rewarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversal_reason", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["referral_id"], ["referrals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipient_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["recipient_organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("referral_id", "recipient_organization_id", name="uq_referral_rewards_recipient"),
        sa.UniqueConstraint("idempotency_key", name="uq_referral_rewards_idempotency_key"),
    )
    for name, columns in (
        ("ix_referral_rewards_referral_id", ["referral_id"]),
        ("ix_referral_rewards_recipient_user_id", ["recipient_user_id"]),
        ("ix_referral_rewards_recipient_organization_id", ["recipient_organization_id"]),
        ("ix_referral_rewards_status", ["status"]),
    ):
        op.create_index(name, "referral_rewards", columns)


def downgrade() -> None:
    for name in (
        "ix_referral_rewards_status",
        "ix_referral_rewards_recipient_organization_id",
        "ix_referral_rewards_recipient_user_id",
        "ix_referral_rewards_referral_id",
    ):
        op.drop_index(name, table_name="referral_rewards")
    op.drop_table("referral_rewards")
    for name in (
        "ix_referrals_invitee_status",
        "ix_referrals_referrer_status",
        "ix_referrals_status",
        "ix_referrals_subscription_id",
        "ix_referrals_invitee_organization_id",
        "ix_referrals_invitee_user_id",
        "ix_referrals_referrer_organization_id",
        "ix_referrals_referrer_user_id",
        "ix_referrals_referral_code_id",
    ):
        op.drop_index(name, table_name="referrals")
    op.drop_table("referrals")
    op.drop_index("ix_referral_codes_active", table_name="referral_codes")
    op.drop_index("ix_referral_codes_code", table_name="referral_codes")
    op.drop_index("ix_referral_codes_created_by_user_id", table_name="referral_codes")
    op.drop_index("ix_referral_codes_organization_id", table_name="referral_codes")
    op.drop_table("referral_codes")
