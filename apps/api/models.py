"""Database models for the AgentPGO control and trace planes."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def new_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    memberships: Mapped[list[Membership]] = relationship(back_populates="user", cascade="all, delete-orphan")
    sessions: Mapped[list[AuthSession]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "organization_id", name="uq_memberships_user_organization"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    user: Mapped[User] = relationship(back_populates="memberships")
    organization: Mapped[Organization] = relationship(back_populates="memberships")


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    user: Mapped[User] = relationship(back_populates="sessions")


class ReferralCode(Base):
    """One shareable early-access code owned by a Pro organization."""

    __tablename__ = "referral_codes"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_referral_codes_organization"),
        UniqueConstraint("code", name="uq_referral_codes_code"),
        Index("ix_referral_codes_active", "code", "active"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    created_by_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    organization: Mapped[Organization] = relationship(back_populates="referral_codes")
    created_by: Mapped[User] = relationship()
    referrals: Mapped[list[Referral]] = relationship(back_populates="code", cascade="all, delete-orphan")


class Referral(Base):
    """Attribution between a Pro referrer and a newly-created workspace."""

    __tablename__ = "referrals"
    __table_args__ = (
        UniqueConstraint("invitee_organization_id", name="uq_referrals_invitee_organization"),
        Index("ix_referrals_referrer_status", "referrer_organization_id", "status"),
        Index("ix_referrals_invitee_status", "invitee_organization_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    referral_code_id: Mapped[str] = mapped_column(String(36), ForeignKey("referral_codes.id", ondelete="CASCADE"), nullable=False, index=True)
    referrer_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    referrer_organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    invitee_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    invitee_organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", index=True)
    subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    attribution_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    qualified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    code: Mapped[ReferralCode] = relationship(back_populates="referrals")
    referrer_organization: Mapped[Organization] = relationship(foreign_keys=[referrer_organization_id], back_populates="referrals")
    invitee_organization: Mapped[Organization] = relationship(foreign_keys=[invitee_organization_id])
    referrer_user: Mapped[User] = relationship(foreign_keys=[referrer_user_id])
    invitee_user: Mapped[User] = relationship(foreign_keys=[invitee_user_id])
    rewards: Mapped[list[ReferralReward]] = relationship(back_populates="referral", cascade="all, delete-orphan")


class ReferralReward(Base):
    """Idempotent free-Pro-month reward ledger entry."""

    __tablename__ = "referral_rewards"
    __table_args__ = (
        UniqueConstraint("referral_id", "recipient_organization_id", name="uq_referral_rewards_recipient"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    referral_id: Mapped[str] = mapped_column(String(36), ForeignKey("referrals.id", ondelete="CASCADE"), nullable=False, index=True)
    recipient_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    recipient_organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    reward_type: Mapped[str] = mapped_column(String(32), nullable=False, default="FREE_PRO_MONTH")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    provider_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rewarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reversal_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    referral: Mapped[Referral] = relationship(back_populates="rewards")
    recipient_organization: Mapped[Organization] = relationship(foreign_keys=[recipient_organization_id], back_populates="referral_rewards")
    recipient_user: Mapped[User] = relationship(foreign_keys=[recipient_user_id])


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan: Mapped[str] = mapped_column(String(16), nullable=False, default="free")
    plan_status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    plan_source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    plan_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Dodo identifiers are non-secret references used to reconcile webhook
    # deliveries with the owning workspace. Provider credentials never live
    # in this table.
    dodo_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    dodo_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    dodo_subscription_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    projects: Mapped[list[Project]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    traces: Mapped[list[Trace]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    memberships: Mapped[list[Membership]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    referral_codes: Mapped[list[ReferralCode]] = relationship(back_populates="organization", cascade="all, delete-orphan")
    referrals: Mapped[list[Referral]] = relationship(
        back_populates="referrer_organization",
        foreign_keys="Referral.referrer_organization_id",
        cascade="all, delete-orphan",
    )
    referral_rewards: Mapped[list[ReferralReward]] = relationship(
        back_populates="recipient_organization",
        foreign_keys="ReferralReward.recipient_organization_id",
        cascade="all, delete-orphan",
    )
    billing_checkouts: Mapped[list[BillingCheckout]] = relationship(back_populates="organization", cascade="all, delete-orphan")


class BillingCheckout(Base):
    """Server-created Dodo checkout session and its idempotency record."""

    __tablename__ = "billing_checkouts"
    __table_args__ = (
        UniqueConstraint("organization_id", "idempotency_key", name="uq_billing_checkouts_org_idempotency"),
        Index("ix_billing_checkouts_provider_session", "provider_session_id"),
        Index("ix_billing_checkouts_organization_created", "organization_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    plan: Mapped[str] = mapped_column(String(16), nullable=False, default="pro")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_session_id: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    checkout_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    organization: Mapped[Organization] = relationship(back_populates="billing_checkouts")


class BillingWebhookEvent(Base):
    """Durable Dodo webhook receipt used for signature/audit/idempotency."""

    __tablename__ = "billing_webhook_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider_event_id: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="received", index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("organization_id", "slug", name="uq_projects_organization_slug"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    organization: Mapped[Organization] = relationship(back_populates="projects")
    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="project")
    traces: Mapped[list[Trace]] = relationship(back_populates="project", cascade="all, delete-orphan")
    versions: Mapped[list[ProjectVersion]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="ProjectVersion.created_at"
    )
    settings: Mapped[ProjectSettings | None] = relationship(
        back_populates="project", cascade="all, delete-orphan", uselist=False
    )
    layouts: Mapped[list[ProjectLayout]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="ProjectLayout.revision"
    )


class ProjectVersion(Base):
    """Immutable agent graph and aggregate metrics for one project release."""

    __tablename__ = "project_versions"
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_project_versions_project_version"),
        Index("ix_project_versions_organization_created", "organization_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[str] = mapped_column(String(64), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False, default="STAGING")
    run_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    total_executions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    baseline_cost: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False, default=0)
    optimized_cost: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False, default=0)
    savings_pct: Mapped[float] = mapped_column(Numeric(9, 4), nullable=False, default=0)
    monthly_savings_estimate: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False, default=0)
    monthly_requests: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    baseline_latency_p95: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    optimized_latency_p95: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    baseline_quality: Mapped[float] = mapped_column(Numeric(9, 4), nullable=False, default=0)
    optimized_quality: Mapped[float] = mapped_column(Numeric(9, 4), nullable=False, default=0)
    eval_cases_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quality_tolerance_pct: Mapped[float] = mapped_column(Numeric(9, 4), nullable=False, default=1)
    confidence_pct: Mapped[float] = mapped_column(Numeric(9, 4), nullable=False, default=95)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="versions")
    nodes: Mapped[list[AgentNode]] = relationship(
        back_populates="version", cascade="all, delete-orphan", order_by="AgentNode.ordinal"
    )
    edges: Mapped[list[GraphEdge]] = relationship(
        back_populates="version", cascade="all, delete-orphan", order_by="GraphEdge.ordinal"
    )


class AgentNode(Base):
    """Persisted node telemetry/configuration for a project version."""

    __tablename__ = "agent_nodes"
    __table_args__ = (
        UniqueConstraint("version_id", "node_id", name="uq_agent_nodes_version_node"),
        Index("ix_agent_nodes_project", "project_id", "version_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("project_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    node_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    x: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    y: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    baseline_model: Mapped[str] = mapped_column(String(255), nullable=False)
    current_model: Mapped[str] = mapped_column(String(255), nullable=False)
    optimized_model: Mapped[str] = mapped_column(String(255), nullable=False)
    calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_cost: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False, default=0)
    baseline_cost: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False, default=0)
    optimized_cost: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False, default=0)
    latency_sec: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    baseline_latency_sec: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    optimized_latency_sec: Mapped[float] = mapped_column(Numeric(12, 4), nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_share_pct: Mapped[float] = mapped_column(Numeric(9, 4), nullable=False, default=0)
    quality_sensitivity: Mapped[str] = mapped_column(String(16), nullable=False, default="MEDIUM")
    is_hotspot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    prompt_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    candidates: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    version: Mapped[ProjectVersion] = relationship(back_populates="nodes")


class GraphEdge(Base):
    """Directed execution edge between nodes in one immutable graph snapshot."""

    __tablename__ = "graph_edges"
    __table_args__ = (
        UniqueConstraint("version_id", "edge_id", name="uq_graph_edges_version_edge"),
        Index("ix_graph_edges_project", "project_id", "version_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("project_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    edge_id: Mapped[str] = mapped_column(String(255), nullable=False)
    from_node: Mapped[str] = mapped_column(String(255), nullable=False)
    to_node: Mapped[str] = mapped_column(String(255), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    throughput_tokens_per_sec: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    avg_latency_ms: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False, default=0)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    version: Mapped[ProjectVersion] = relationship(back_populates="edges")


class ProjectSettings(Base):
    """Mutable, tenant-scoped browser optimization settings."""

    __tablename__ = "project_settings"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    quality_tolerance_pct: Mapped[float] = mapped_column(Numeric(9, 4), nullable=False, default=1)
    confidence_pct: Mapped[float] = mapped_column(Numeric(9, 4), nullable=False, default=95)
    max_p95_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    objective: Mapped[dict] = mapped_column(JSON, nullable=False, default=lambda: {"minimize": ["cost", "latency"]})
    allowed_models: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="settings")


class ProjectLayout(Base):
    """Revisioned UI-only node positions; never used as execution truth."""

    __tablename__ = "project_layouts"
    __table_args__ = (
        UniqueConstraint("project_id", "revision", name="uq_project_layouts_project_revision"),
        Index("ix_project_layouts_project_updated", "project_id", "updated_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("project_versions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    nodes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="layouts")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="default")
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    organization: Mapped[Organization] = relationship(back_populates="api_keys")
    project: Mapped[Project | None] = relationship(back_populates="api_keys")


class Trace(Base):
    """One normalized OTLP span.

    OTLP calls contain resource/scope/span batches.  Storing one row per span
    keeps ingestion idempotent and makes downstream profiling query-friendly.
    ``raw_span`` retains the canonical span object for forward compatibility.
    """

    __tablename__ = "traces"
    __table_args__ = (
        UniqueConstraint("project_id", "trace_id", "span_id", name="uq_traces_project_trace_span"),
        Index("ix_traces_project_received", "project_id", "received_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False)
    span_id: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_span_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    name: Mapped[str] = mapped_column(String(1024), nullable=False)
    kind: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    service_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resource_attributes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    attributes: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    events: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    links: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    scope: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    raw_span: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    organization: Mapped[Organization] = relationship(back_populates="traces")
    project: Mapped[Project] = relationship(back_populates="traces")


class Job(Base):
    """Durable unit of asynchronous work."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_claimable", "status", "available_at", "claim_expires_at"),
        Index("ix_jobs_organization_created", "organization_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    checkpoint: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    max_experiment_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=25.0)
    spent_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    claimed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    project_version_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("project_versions.id", ondelete="SET NULL"), nullable=True, index=True)
    dataset_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("eval_datasets.id", ondelete="SET NULL"), nullable=True, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    objective: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quality_tolerance_pp: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)
    confidence_pct: Mapped[float | None] = mapped_column(Numeric(9, 4), nullable=True)
    allowed_models: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organization: Mapped[Organization] = relationship()
    project: Mapped[Project | None] = relationship()
    candidate_results: Mapped[list[JobCandidateResult]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="JobCandidateResult.created_at"
    )
    optimization_result: Mapped[OptimizationResult | None] = relationship(
        back_populates="job", cascade="all, delete-orphan", uselist=False
    )
    optimization_events: Mapped[list[OptimizationEvent]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="OptimizationEvent.sequence"
    )


class JobCandidateResult(Base):
    """Idempotent result for one candidate within a job."""

    __tablename__ = "job_candidate_results"
    __table_args__ = (
        UniqueConstraint("job_id", "candidate_id", name="uq_job_candidate_results_job_candidate"),
        Index("ix_job_candidate_results_job_id", "job_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    candidate_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed")
    result: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    job: Mapped[Job] = relationship(back_populates="candidate_results")


class OptimizationEvent(Base):
    """Append-only, replayable event for an optimization run."""

    __tablename__ = "optimization_events"
    __table_args__ = (
        UniqueConstraint("job_id", "sequence", name="uq_optimization_events_job_sequence"),
        UniqueConstraint("job_id", "event_id", name="uq_optimization_events_job_event"),
        Index("ix_optimization_events_job_created", "job_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False, default="INFO")
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    job: Mapped[Job] = relationship(back_populates="optimization_events")


class OptimizationIdempotency(Base):
    """Request key mapping for replay-safe optimization starts."""

    __tablename__ = "optimization_idempotency"
    __table_args__ = (
        UniqueConstraint("organization_id", "operation", "idempotency_key", name="uq_optimization_idempotency_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)



# ``Job`` intentionally stores the wire field as ``status``; callers may use
# ``state`` as the domain term without creating a second persisted column.
Job.state = property(lambda self: self.status, lambda self, value: setattr(self, "status", value))  # type: ignore[attr-defined]


class EvalDataset(Base):
    """Immutable-by-version evaluation dataset metadata."""
    __tablename__ = "eval_datasets"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", "version", name="uq_eval_datasets_organization_name_version"),
        Index("ix_eval_datasets_organization_created", "organization_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    cases: Mapped[list[EvalCase]] = relationship(back_populates="dataset", cascade="all, delete-orphan", order_by="EvalCase.ordinal")
    graders: Mapped[list[EvalGrader]] = relationship(back_populates="dataset", cascade="all, delete-orphan", order_by="EvalGrader.ordinal")
    runs: Mapped[list[EvalRun]] = relationship(back_populates="dataset", cascade="all, delete-orphan")


class EvalCase(Base):
    """One input/expected example in a dataset version."""
    __tablename__ = "eval_cases"
    __table_args__ = (
        UniqueConstraint("dataset_id", "case_id", name="uq_eval_cases_dataset_case"),
        Index("ix_eval_cases_dataset_ordinal", "dataset_id", "ordinal"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_id: Mapped[str] = mapped_column(String(36), ForeignKey("eval_datasets.id", ondelete="CASCADE"), nullable=False, index=True)
    case_id: Mapped[str] = mapped_column(String(255), nullable=False)
    input_data: Mapped[dict | str] = mapped_column("input", JSON, nullable=False)
    expected: Mapped[object] = mapped_column(JSON, nullable=False)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    dataset: Mapped[EvalDataset] = relationship(back_populates="cases")


class EvalGrader(Base):
    """Serializable deterministic grader configuration for a dataset."""
    __tablename__ = "eval_graders"
    __table_args__ = (
        UniqueConstraint("dataset_id", "name", name="uq_eval_graders_dataset_name"),
        Index("ix_eval_graders_dataset_ordinal", "dataset_id", "ordinal"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    dataset_id: Mapped[str] = mapped_column(String(36), ForeignKey("eval_datasets.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    dataset: Mapped[EvalDataset] = relationship(back_populates="graders")


class EvalRun(Base):
    """Durable execution of one immutable evaluation suite version."""

    __tablename__ = "eval_runs"
    __table_args__ = (
        Index("ix_eval_runs_organization_created", "organization_id", "created_at"),
        Index("ix_eval_runs_project_created", "project_id", "created_at"),
        Index("ix_eval_runs_suite_created", "eval_suite_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    eval_suite_id: Mapped[str] = mapped_column(String(36), ForeignKey("eval_datasets.id", ondelete="CASCADE"), nullable=False, index=True)
    project_version_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("project_versions.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    candidate_config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    grader_snapshot: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    aggregate_metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organization: Mapped[Organization] = relationship()
    project: Mapped[Project] = relationship()
    dataset: Mapped[EvalDataset] = relationship(back_populates="runs")
    cases: Mapped[list[EvalRunCase]] = relationship(back_populates="run", cascade="all, delete-orphan", order_by="EvalRunCase.ordinal")


class EvalRunCase(Base):
    """Persisted per-case evidence for an evaluation run."""

    __tablename__ = "eval_run_cases"
    __table_args__ = (
        UniqueConstraint("eval_run_id", "case_id", name="uq_eval_run_cases_run_case"),
        Index("ix_eval_run_cases_run_ordinal", "eval_run_id", "ordinal"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    eval_run_id: Mapped[str] = mapped_column(String(36), ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    case_id: Mapped[str] = mapped_column(String(255), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    run: Mapped[EvalRun] = relationship(back_populates="cases")


class OutboxEvent(Base):
    """Transactional event waiting for publication to the queue."""
    __tablename__ = "outbox_events"
    __table_args__ = (
        Index("ix_outbox_events_claimable", "status", "available_at", "created_at"),
        Index("ix_outbox_events_aggregate", "aggregate_type", "aggregate_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    @property
    def event_name(self) -> str:
        return self.event_type
    @event_name.setter
    def event_name(self, value: str) -> None:
        self.event_type = value


class OptimizationResult(Base):
    """Durable recommendation and result metadata for one optimization job."""
    __tablename__ = "optimization_results"
    __table_args__ = (Index("ix_optimization_results_organization_created", "organization_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    job_id: Mapped[str] = mapped_column(String(36), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, unique=True)
    schema_version: Mapped[str] = mapped_column(String(16), nullable=False, default="v1")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    recommendation: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)
    job: Mapped[Job] = relationship(back_populates="optimization_result")
    @property
    def result(self) -> dict:
        return self.recommendation
    @result.setter
    def result(self, value: dict) -> None:
        self.recommendation = value
