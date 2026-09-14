"""FastAPI application, OTLP ingestion, and durable control-plane API."""
from __future__ import annotations

import json
import hashlib
import asyncio
import os
import re
import time
from datetime import datetime, timezone
from statistics import quantiles
from typing import Any
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from . import models as api_models
from .auth import (
    Tenant,
    _configured_demo_tenant,
    _demo_enabled,
    authenticate,
    authenticate_user,
    hash_password,
    issue_api_key,
    issue_demo_token,
    issue_session,
    normalize_email,
    revoke_api_key,
    revoke_session,
    session_token,
    verify_password,
)
from .db import create_session_factory, session_dependency
from .models import AuthSession, Job, Membership, Organization, Project, ProjectLayout, ProjectSettings, ProjectVersion, Referral, ReferralCode, ReferralReward, Trace, User, utc_now
from .project_serializers import (
    GraphValidationError,
    serialize_layout,
    serialize_project,
    serialize_settings,
    serialize_version,
    validate_graph,
)
from .schemas import IngestionResponse, OTLPExportRequest
from .trace_serializers import decode_cursor, encode_cursor, serialize_trace, serialize_trace_detail, trace_metrics
from services.worker.queue import SQSQueuePublisher
from services.billing.referrals import (
    ReferralPolicyError,
    attribute_signup,
    find_valid_code,
    get_or_create_code,
    referral_summary,
)

try:
    from services.contracts import JobPayload
except ImportError:  # pragma: no cover - compatibility with pre-contract installs
    JobPayload = None  # type: ignore[assignment,misc]

MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_EVAL_CASES = 1_000
MAX_EVAL_GRADERS = 64
MAX_CONFIG_CANDIDATES = 256
MAX_CONFIG_BYTES = 256 * 1024
MAX_TRACE_PAGE_SIZE = 100
PLAN_LIMITS: dict[str, dict[str, int | float]] = {
    "free": {
        "maxProjects": 3,
        "maxTraceSpansPerMonth": 100_000,
        "maxEvalCases": 1_000,
        "maxOptimizationRunsPerMonth": 5,
        "maxProviderSpendUsdPerMonth": 25,
        "maxExperimentCostUsd": 25,
        "retentionDays": 7,
    },
    "pro": {
        "maxProjects": 25,
        "maxTraceSpansPerMonth": 1_000_000,
        "maxEvalCases": 100_000,
        "maxOptimizationRunsPerMonth": 100,
        "maxProviderSpendUsdPerMonth": 500,
        "maxExperimentCostUsd": 500,
        "retentionDays": 90,
    },
}
DEFAULT_APP_ORIGIN = "https://2syexxoronpapxxxhzu6grgi4a0limkr.lambda-url.us-east-1.on.aws"
CONTENT_KEY = re.compile(r"(?:^|[._-])(prompt|input|completion|output|content|message|messages|secret|password|authorization|api[-_]?key)(?:$|[._-])", re.I)
REDACTED = "[REDACTED]"


def _month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _entitlement_snapshot(organization_id: str, session: Session) -> dict[str, Any]:
    organization = session.get(Organization, organization_id)
    if organization is None:
        raise HTTPException(status_code=401, detail="Organization no longer exists")
    requested_plan = str(organization.plan or "free").lower()
    if requested_plan not in PLAN_LIMITS:
        requested_plan = "free"
    status = str(organization.plan_status or "active").lower()
    expires_at = organization.plan_expires_at
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            status = "expired"
    # Paid capabilities are only active when the entitlement state is active.
    # A canceled/past-due/expired Pro organization keeps its source metadata
    # but is evaluated against Free limits until reactivated.
    plan = requested_plan if requested_plan == "free" or status == "active" else "free"
    period_start = _month_start()
    project_count = int(session.scalar(select(func.count(Project.id)).where(Project.organization_id == organization_id)) or 0)
    trace_count = int(session.scalar(select(func.count(Trace.id)).where(Trace.organization_id == organization_id, Trace.received_at >= period_start)) or 0)
    dataset_cls = getattr(api_models, "EvalDataset", None)
    case_cls = getattr(api_models, "EvalCase", None)
    eval_case_count = 0
    if dataset_cls is not None and case_cls is not None:
        eval_case_count = int(session.scalar(select(func.count(case_cls.id)).join(dataset_cls, case_cls.dataset_id == dataset_cls.id).where(dataset_cls.organization_id == organization_id)) or 0)
    optimization_count = int(session.scalar(select(func.count(Job.id)).where(Job.organization_id == organization_id, Job.kind == "optimization", Job.created_at >= period_start)) or 0)
    limits = PLAN_LIMITS[plan]
    usage = {
        "projects": project_count,
        "traceSpansThisMonth": trace_count,
        "evalCases": eval_case_count,
        "optimizationRunsThisMonth": optimization_count,
    }
    return {
        "organizationId": organization_id,
        "plan": plan,
        "status": str(organization.plan_status or "active").lower(),
        "source": str(organization.plan_source or "manual"),
        "limits": dict(limits),
        "usage": usage,
        "periodStart": period_start.isoformat(),
        "planExpiresAt": expires_at.isoformat() if expires_at else None,
        "features": {
            "optimization": True,
            "hostedTraces": True,
            "scheduledOptimization": plan == "pro",
            "teamAccess": plan == "pro",
        },
    }


def _enforce_quota(organization_id: str, resource: str, amount: int | float, session: Session) -> None:
    snapshot = _entitlement_snapshot(organization_id, session)
    limit_name = {
        "projects": "maxProjects",
        "traceSpans": "maxTraceSpansPerMonth",
        "evalCases": "maxEvalCases",
        "optimizationRuns": "maxOptimizationRunsPerMonth",
    }[resource]
    usage_name = {
        "projects": "projects",
        "traceSpans": "traceSpansThisMonth",
        "evalCases": "evalCases",
        "optimizationRuns": "optimizationRunsThisMonth",
    }[resource]
    if float(snapshot["usage"][usage_name]) + float(amount) > float(snapshot["limits"][limit_name]):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ENTITLEMENT_LIMIT_REACHED",
                "message": f"{resource} quota exceeded for the {snapshot['plan']} plan",
                "fields": {
                    "plan": snapshot["plan"],
                    "limit": limit_name,
                    "usage": snapshot["usage"][usage_name],
                    "requested": amount,
                },
            },
        )


def _request_id(request: Request) -> str:
    """Return a bounded, log-safe request ID, preserving client correlation."""
    supplied = request.headers.get("x-request-id", "").strip()
    if supplied and len(supplied) <= 128 and re.fullmatch(r"[A-Za-z0-9._:-]+", supplied):
        return supplied
    return f"req_{uuid4().hex}"


def _error_code(status_code: int) -> str:
    return {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        413: "PAYLOAD_TOO_LARGE",
        415: "UNSUPPORTED_MEDIA_TYPE",
        422: "VALIDATION_ERROR",
        429: "RATE_LIMITED",
        500: "INTERNAL_SERVER_ERROR",
        503: "SERVICE_UNAVAILABLE",
    }.get(status_code, "REQUEST_FAILED")


def _error_response(request: Request, status_code: int, message: str, *, fields: dict[str, Any] | None = None, headers: dict[str, str] | None = None, code: str | None = None) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None) or _request_id(request)
    body = {
        "error": {
            "code": code or _error_code(status_code),
            "message": message,
            "requestId": request_id,
            "fields": fields or {},
        },
        # Kept as a read-only compatibility field for existing SDK callers.
        # New clients should consume the stable `error` envelope above.
        "detail": message,
    }
    response = JSONResponse(body, status_code=status_code, headers=headers)
    response.headers["x-request-id"] = request_id
    return response


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9_-]*$")


class ProjectApiKeyCreate(BaseModel):
    name: str = Field(default="agentpgo", min_length=1, max_length=255)


class ProjectResponse(BaseModel):
    id: str
    organization_id: str
    name: str
    slug: str


class ProjectSettingsPatch(BaseModel):
    model_config = {"populate_by_name": True}

    quality_tolerance_pp: float | None = Field(default=None, alias="qualityTolerancePp", ge=0, le=100)
    quality_tolerance_pct: float | None = Field(default=None, alias="qualityTolerancePct", ge=0, le=100)
    confidence_pct: float | None = Field(default=None, alias="confidencePct", ge=0, le=100)
    max_p95_latency_ms: int | None = Field(default=None, alias="maxP95LatencyMs", ge=1, le=3_600_000)
    objective: dict[str, Any] | None = None
    allowed_models: list[str] | None = Field(default=None, alias="allowedModels", max_length=256)

    @field_validator("objective")
    @classmethod
    def valid_objective(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None and len(json.dumps(value, default=str)) > 16 * 1024:
            raise ValueError("objective is too large")
        return value


class LayoutWrite(BaseModel):
    model_config = {"populate_by_name": True}

    version_id: str | None = Field(default=None, alias="versionId", max_length=255)
    revision: int | None = Field(default=None, ge=0)
    nodes: dict[str, dict[str, float]] = Field(default_factory=dict, max_length=512)

    @field_validator("nodes")
    @classmethod
    def valid_nodes(cls, value: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
        for node_id, position in value.items():
            if not node_id or len(node_id) > 255 or set(position) != {"x", "y"}:
                raise ValueError("layout nodes must contain finite x/y positions")
            if not all(isinstance(coordinate, (int, float)) and abs(coordinate) <= 1e7 for coordinate in position.values()):
                raise ValueError("layout coordinates are out of range")
        return value


class VersionCreate(BaseModel):
    model_config = {"populate_by_name": True}

    version: str = Field(min_length=1, max_length=64)
    environment: str = Field(default="STAGING", min_length=1, max_length=32)
    run_id: str | None = Field(default=None, alias="runId", max_length=255)
    nodes: list[dict[str, Any]] = Field(default_factory=list, max_length=512)
    edges: list[dict[str, Any]] = Field(default_factory=list, max_length=1024)
    metrics: dict[str, Any] = Field(default_factory=dict, max_length=64)

    @field_validator("nodes", "edges")
    @classmethod
    def bounded_graph_values(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(json.dumps(value, default=str)) > 512 * 1024:
            raise ValueError("graph payload is too large")
        return value


class EvalCreate(BaseModel):
    project_id: str | None = Field(default=None, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    cases: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_EVAL_CASES)
    graders: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_EVAL_GRADERS)


class EvalSuiteCreate(BaseModel):
    """Canonical project-scoped evaluation suite input."""

    name: str = Field(min_length=1, max_length=255)
    cases: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_EVAL_CASES)
    graders: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_EVAL_GRADERS)
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=64)

    @field_validator("name")
    @classmethod
    def normalized_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError("name must not be blank")
        return name


class EvalRunCreate(BaseModel):
    """Start one evaluation against a persisted suite snapshot."""

    model_config = {"populate_by_name": True}

    project_version_id: str | None = Field(default=None, alias="projectVersionId", max_length=255)
    candidate_config: dict[str, Any] = Field(default_factory=dict, alias="candidateConfig")
    sample_count: int | None = Field(default=None, alias="sampleCount", ge=1, le=MAX_EVAL_CASES)

    @field_validator("candidate_config")
    @classmethod
    def bounded_candidate_config(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, default=str)) > MAX_CONFIG_BYTES:
            raise ValueError("candidateConfig exceeds the maximum size")
        return value


class RunCreate(BaseModel):
    model_config = {"populate_by_name": True}

    project_id: str | None = Field(default=None, max_length=255)
    dataset_id: str | None = Field(default=None, max_length=255)
    project_version_id: str | None = Field(default=None, alias="projectVersionId", max_length=255)
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey", min_length=1, max_length=255)
    objective: str | None = Field(default=None, max_length=64)
    quality_tolerance_pp: float | None = Field(default=None, alias="qualityTolerancePp", ge=0, le=100)
    confidence_pct: float | None = Field(default=None, alias="confidencePct", ge=0, le=100)
    allowed_models: list[str] = Field(default_factory=list, alias="allowedModels", max_length=256)
    config: dict[str, Any] = Field(default_factory=dict)
    max_experiment_cost_usd: float = Field(default=25.0, gt=0, le=100000)

    @field_validator("config")
    @classmethod
    def bounded_config(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, default=str)) > MAX_CONFIG_BYTES:
            raise ValueError("config exceeds the maximum size")
        candidates = value.get("candidates", value.get("candidate_configs", []))
        if candidates is not None and (not isinstance(candidates, list) or len(candidates) > MAX_CONFIG_CANDIDATES):
            raise ValueError(f"config supports at most {MAX_CONFIG_CANDIDATES} candidates")
        return value


class RunResponse(BaseModel):
    run_id: str
    status: str


class AuthSignup(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=256)
    referral_code: str | None = Field(default=None, alias="referralCode", min_length=1, max_length=64)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        normalized = normalize_email(value)
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
            raise ValueError("email must be valid")
        return normalized

    @field_validator("name")
    @classmethod
    def non_blank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value.strip()


class AuthSignin(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)

    @field_validator("email")
    @classmethod
    def normalize_login_email(cls, value: str) -> str:
        return normalize_email(value)


class ReferralCodeValidation(BaseModel):
    code: str = Field(min_length=1, max_length=64)


class AuthProfilePatch(BaseModel):
    name: str = Field(min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def non_blank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value.strip()


def _user_payload(user: User) -> dict[str, Any]:
    return {"id": user.id, "name": user.name, "email": user.email}


def _nanos_to_datetime(value: int | str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1_000_000_000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _redact(value: Any, key: str | None = None) -> Any:
    """Recursively remove customer content and credentials at the API edge."""
    if key and CONTENT_KEY.search(key) and not key.lower().endswith(("tokens", "token_count")):
        return REDACTED
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _value(value: Any) -> Any:
    data = value.model_dump(by_alias=True, exclude_none=True)
    for key in ("stringValue", "boolValue", "intValue", "doubleValue", "bytesValue", "arrayValue", "kvlistValue"):
        if key in data:
            return _redact(data[key], key)
    return None


def _attributes(items: list[Any]) -> dict[str, Any]:
    return {item.key: _redact(_value(item.value), item.key) for item in items}


def _spans(payload: OTLPExportRequest):
    for resource_spans in payload.resource_spans:
        resource_attributes = _attributes(resource_spans.resource.attributes) if resource_spans.resource else {}
        service_name = resource_attributes.get("service.name")
        for scope_spans in resource_spans.scope_spans:
            scope = _redact(scope_spans.scope.model_dump(by_alias=True, exclude_none=True) if scope_spans.scope else {})
            for span in scope_spans.spans:
                yield span, resource_attributes, service_name, scope


def _project_for_request(*, request: Request, project_id: str | None, tenant: Tenant, session: Session) -> Project:
    header_project = request.headers.get("X-AgentPGO-Project-ID")
    if project_id and header_project and project_id != header_project:
        raise HTTPException(status_code=403, detail="Project is outside API key scope")
    requested = project_id or header_project
    if tenant.project_id and requested and requested != tenant.project_id:
        raise HTTPException(status_code=403, detail="Project is outside API key scope")
    resolved = tenant.project_id or requested
    if not resolved:
        raise HTTPException(status_code=400, detail="project_id is required for organization API keys")
    project = session.scalar(select(Project).where(Project.id == resolved, Project.organization_id == tenant.organization_id))
    if project is None:
        raise HTTPException(status_code=403, detail="Project is outside API key scope")
    return project


def _project_reference(reference: str | None, *, tenant: Tenant, session: Session) -> Project:
    if tenant.project_id and reference and reference not in {tenant.project_id}:
        # A project-scoped key may also address its own project by slug.
        own = session.scalar(select(Project).where(Project.id == tenant.project_id, Project.organization_id == tenant.organization_id))
        if own is None or reference != own.slug:
            raise HTTPException(status_code=403, detail="Project is outside API key scope")
    resolved = tenant.project_id or reference
    if not resolved:
        raise HTTPException(status_code=400, detail="project_id is required for organization API keys")
    project = session.scalar(select(Project).where(Project.organization_id == tenant.organization_id, or_(Project.id == resolved, Project.slug == resolved)))
    if project is None or (tenant.project_id and project.id != tenant.project_id):
        raise HTTPException(status_code=403, detail="Project is outside API key scope")
    return project


def _require_json(request: Request) -> None:
    content_type = request.headers.get("content-type", "application/json").split(";", 1)[0].strip().lower()
    if content_type not in {"application/json", ""}:
        raise HTTPException(status_code=415, detail="OTLP JSON requires application/json content type")


def _tenant_dependency(session_factory: sessionmaker[Session]):
    get_session = session_dependency(session_factory)

    def get_tenant(request: Request, session: Session = Depends(get_session), authorization: str | None = Header(default=None), x_api_key: str | None = Header(default=None)) -> Tenant:
        return authenticate(request, session, authorization, x_api_key)

    return get_tenant, get_session


def _model(name: str) -> Any:
    model = getattr(api_models, name, None)
    if model is None:
        raise HTTPException(status_code=503, detail=f"{name} persistence is unavailable")
    return model


def _record_outbox(session: Session, tenant: Tenant, aggregate_type: str, aggregate_id: str, event_type: str, payload: dict[str, Any]) -> None:
    cls = getattr(api_models, "OutboxEvent", None)
    if cls is not None:
        session.add(cls(organization_id=tenant.organization_id, aggregate_type=aggregate_type, aggregate_id=aggregate_id, event_type=event_type, dedupe_key=f"{aggregate_id}:{event_type}", payload=payload))


def _dataset_for_request(dataset_id: str, *, tenant: Tenant, project: Project, session: Session) -> Any:
    cls = _model("EvalDataset")
    dataset = session.scalar(select(cls).where(cls.id == dataset_id, cls.organization_id == tenant.organization_id, cls.project_id == project.id))
    if dataset is None:
        raise HTTPException(status_code=404, detail="Evaluation dataset not found")
    return dataset


def _case_values(case: dict[str, Any], index: int) -> dict[str, Any]:
    return {"case_id": str(case.get("case_id", case.get("id", index))), "input_data": case.get("input_data", case.get("input", case.get("prompt", {}))), "expected": case.get("expected", case.get("output", case.get("answer", {}))), "metadata_json": case.get("metadata_json", case.get("metadata", {})), "ordinal": index}


def _create_dataset(body: EvalCreate, *, tenant: Tenant, project: Project, session: Session, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    dataset_cls, case_cls, grader_cls = _model("EvalDataset"), _model("EvalCase"), _model("EvalGrader")
    _enforce_quota(tenant.organization_id, "evalCases", len(body.cases), session)
    dataset = dataset_cls(organization_id=tenant.organization_id, project_id=project.id, name=body.name, version=1, metadata_json=metadata or {})
    session.add(dataset)
    session.flush()
    for index, case in enumerate(body.cases):
        session.add(case_cls(dataset_id=dataset.id, **_case_values(case, index)))
    for index, grader in enumerate(body.graders):
        session.add(grader_cls(dataset_id=dataset.id, name=str(grader.get("name", f"grader-{index}")), kind=str(grader.get("kind", "exact_match")), config=dict(grader.get("config", {})), ordinal=index))
    session.commit()
    session.refresh(dataset)
    return {"id": dataset.id, "project_id": project.id, "organization_id": tenant.organization_id, "name": dataset.name, "cases": body.cases, "graders": body.graders, "version": dataset.version}


def create_app(*, session_factory: sessionmaker[Session] | None = None, database_url: str | None = None, queue_publisher: Any | None = None, dodo_client: Any | None = None) -> FastAPI:
    factory = session_factory or create_session_factory(database_url)
    get_tenant, get_session = _tenant_dependency(factory)
    app = FastAPI(title="AgentPGO API", version="1.0.0")
    configured_origin = os.getenv("APP_ORIGIN")
    if not configured_origin and os.getenv("APP_ENV", "development").strip().lower() != "production":
        configured_origin = DEFAULT_APP_ORIGIN
    # Never use a wildcard origin with credentials. An empty production
    # origin intentionally means browser requests are denied until deployment
    # supplies the real HTTPS frontend origin.
    normalized_origin = configured_origin.strip().rstrip("/") if configured_origin else ""
    # Credentialed CORS with `*` is unsafe and rejected rather than silently
    # producing a browser policy that exposes authenticated responses.
    allowed_origins = [] if normalized_origin in {"", "*"} else [normalized_origin]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-AgentPGO-API-Key", "X-AgentPGO-Project-ID", "X-Request-ID", "Idempotency-Key"],
        expose_headers=["x-request-id"],
    )
    app.state.session_factory = factory
    app.state.queue_publisher = queue_publisher
    # Billing uses an injectable provider boundary; production defaults to a
    # small REST client, while tests inject a deterministic fake.
    from .billing import HttpDodoClient, register_billing_routes
    app.state.dodo_client = dodo_client or HttpDodoClient()
    if app.state.queue_publisher is None and os.getenv("SQS_QUEUE_URL"):
        app.state.queue_publisher = SQSQueuePublisher(os.environ["SQS_QUEUE_URL"])

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = _request_id(request)
        # The /api/v1 aliases are registered as real routes below. Keep the
        # original path available for future diagnostics without rewriting the
        # request scope (BaseHTTPMiddleware routes from its original scope).
        if request.method in {"POST", "PUT", "PATCH"}:
            raw_length = request.headers.get("content-length")
            try:
                if raw_length and int(raw_length) > MAX_REQUEST_BYTES:
                    return _error_response(request, 413, "Request body exceeds maximum size.")
            except ValueError:
                return _error_response(request, 400, "Invalid content length.")
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        return response

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        detail = exc.detail
        fields: dict[str, Any] = {}
        code: str | None = None
        if isinstance(detail, dict):
            message = str(detail.get("message", "Request failed."))
            if isinstance(detail.get("fields"), dict):
                fields = detail["fields"]
            if detail.get("code"):
                code = str(detail["code"])
        elif isinstance(detail, list):
            message = "Request validation failed."
            fields = {str(index): str(item) for index, item in enumerate(detail)}
        else:
            message = str(detail) if detail else "Request failed."
        return _error_response(request, exc.status_code, message, fields=fields, headers=exc.headers, code=code)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        fields: dict[str, str] = {}
        for item in exc.errors():
            location = ".".join(str(part) for part in item.get("loc", ()) if part != "body") or "request"
            fields[location] = str(item.get("type", "invalid"))
        return _error_response(request, 422, "Request validation failed.", fields=fields)

    @app.exception_handler(Exception)
    async def internal_exception_handler(request: Request, exc: Exception):
        # Do not expose provider/database details or stack traces to clients.
        return _error_response(request, 500, "Internal server error.")

    @app.get("/health", tags=["system"])
    @app.get("/v1/health", include_in_schema=False, tags=["system"])
    def health() -> dict[str, str]: return {"status": "ok"}

    @app.get("/ready", tags=["system"])
    @app.get("/v1/ready", include_in_schema=False, tags=["system"])
    def ready() -> dict[str, str]:
        try:
            with factory() as session: session.execute(select(1))
        except Exception as exc: raise HTTPException(status_code=503, detail="database is not ready") from exc
        return {"status": "ready"}

    def get_user(
        request: Request,
        session: Session = Depends(get_session),
        authorization: str | None = Header(default=None),
    ) -> User:
        return authenticate_user(request, session, authorization)

    def auth_result(user: User, raw_token: str, expires_in: int) -> dict[str, Any]:
        return {"user": _user_payload(user), "accessToken": raw_token, "tokenType": "Bearer", "expiresIn": expires_in}

    @app.post("/v1/auth/signup", status_code=201, tags=["auth"])
    def signup(body: AuthSignup, session: Session = Depends(get_session)) -> dict[str, Any]:
        email = normalize_email(body.email)
        if session.scalar(select(User).where(User.email == email)) is not None:
            raise HTTPException(status_code=409, detail="Unable to create account with those details")
        user = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password))
        organization = Organization(name=f"{body.name.strip()}'s Workspace")
        session.add_all([user, organization])
        session.flush()
        session.add(Membership(user_id=user.id, organization_id=organization.id, role="owner"))
        if body.referral_code:
            try:
                attribute_signup(
                    session,
                    code=body.referral_code,
                    invitee_user=user,
                    invitee_organization=organization,
                )
            except ReferralPolicyError as exc:
                session.rollback()
                raise HTTPException(status_code=422, detail={"code": exc.code, "message": exc.message}) from exc
        raw_token, auth_session = issue_session(user_id=user.id)
        session.add(auth_session)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail="Unable to create account with those details") from exc
        return auth_result(user, raw_token, 60 * 60 * 24 * 30)

    @app.post("/v1/referrals/code", status_code=201, tags=["referrals"])
    def create_referral_code(response: Response, user: User = Depends(get_user), session: Session = Depends(get_session)) -> dict[str, Any]:
        membership = session.scalar(select(Membership).where(Membership.user_id == user.id).order_by(Membership.created_at))
        if membership is None:
            raise HTTPException(status_code=403, detail="User has no organization membership")
        organization = session.get(Organization, membership.organization_id)
        if organization is None:
            raise HTTPException(status_code=403, detail="Organization no longer exists")
        existing = session.scalar(select(ReferralCode).where(ReferralCode.organization_id == organization.id, ReferralCode.active.is_(True)))
        try:
            code = get_or_create_code(session, organization=organization, user=user)
            session.commit()
        except ReferralPolicyError as exc:
            session.rollback()
            raise HTTPException(status_code=403, detail={"code": exc.code, "message": exc.message}) from exc
        if existing is not None:
            response.status_code = 200
        return {"id": code.id, "code": code.code, "active": code.active, "createdAt": code.created_at.isoformat()}

    @app.get("/v1/referrals", tags=["referrals"])
    def list_referrals(user: User = Depends(get_user), session: Session = Depends(get_session)) -> dict[str, Any]:
        membership = session.scalar(select(Membership).where(Membership.user_id == user.id).order_by(Membership.created_at))
        if membership is None:
            raise HTTPException(status_code=403, detail="User has no organization membership")
        organization_id = membership.organization_id
        code = session.scalar(select(ReferralCode).where(ReferralCode.organization_id == organization_id, ReferralCode.active.is_(True)))
        rows = list(session.scalars(select(Referral).where(Referral.referrer_organization_id == organization_id).order_by(Referral.created_at.desc())).all())
        summary = referral_summary(session, organization_id=organization_id)
        referral_code = code.code if code else None
        app_origin = (os.getenv("APP_ORIGIN") or DEFAULT_APP_ORIGIN).rstrip("/")
        return {
            "code": referral_code,
            "referralCode": referral_code,
            "referralLink": f"{app_origin}/signup?ref={referral_code}" if referral_code else None,
            "referrals": [
                {
                    "id": row.id,
                    "status": row.status,
                    "inviteeEmail": row.invitee_user.email if row.invitee_user else None,
                    "subscriptionId": row.subscription_id,
                    "expiresAt": row.attribution_expires_at.isoformat(),
                    "qualifiedAt": row.qualified_at.isoformat() if row.qualified_at else None,
                    "rewardCount": len(row.rewards),
                }
                for row in rows
            ],
            "summary": summary,
            # Flat aliases are the shape consumed by the account surface.
            **summary,
            "freeProMonths": sum(
                1 for row in rows for reward in row.rewards if reward.status == "REWARDED"
            ),
        }

    @app.post("/v1/referrals/validate", tags=["referrals"])
    def validate_referral_code(body: ReferralCodeValidation, session: Session = Depends(get_session)) -> dict[str, Any]:
        code = body.code.strip().upper()
        return {"valid": find_valid_code(session, code) is not None, "code": code}

    @app.get("/v1/entitlements", tags=["billing"])
    def entitlements(tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        snapshot = _entitlement_snapshot(tenant.organization_id, session)
        summary = referral_summary(session, organization_id=tenant.organization_id)
        snapshot["referrals"] = summary
        code = session.scalar(select(ReferralCode).where(ReferralCode.organization_id == tenant.organization_id, ReferralCode.active.is_(True)))
        snapshot["referralCode"] = code.code if code else None
        # The browser account surface historically called this field
        # ``planStatus``. Keep the canonical lower-case status as well so old
        # SDKs and the current client can consume one response.
        snapshot["planStatus"] = snapshot["status"]
        snapshot["pendingReferrals"] = summary["pending"]
        snapshot["qualifiedReferrals"] = summary["qualified"]
        snapshot["earnedRewards"] = summary["rewarded"]
        return snapshot

    @app.post("/v1/auth/signin", tags=["auth"])
    def signin(body: AuthSignin, session: Session = Depends(get_session)) -> dict[str, Any]:
        user = session.scalar(select(User).where(User.email == normalize_email(body.email)))
        if user is None or user.disabled_at is not None or not verify_password(user.password_hash, body.password):
            raise HTTPException(status_code=401, detail="Invalid email or password", headers={"WWW-Authenticate": "Bearer"})
        user.last_login_at = utc_now()
        raw_token, auth_session = issue_session(user_id=user.id)
        session.add(auth_session)
        session.commit()
        return auth_result(user, raw_token, 60 * 60 * 24 * 30)

    @app.post("/v1/auth/refresh", tags=["auth"])
    def refresh(
        request: Request,
        session: Session = Depends(get_session),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        user = authenticate_user(request, session, authorization)
        raw_old = session_token(authorization)
        if raw_old is None:
            raise HTTPException(status_code=401, detail="Authentication required", headers={"WWW-Authenticate": "Bearer"})
        revoke_session(raw_old, session)
        raw_token, auth_session = issue_session(user_id=user.id)
        session.add(auth_session)
        session.commit()
        return auth_result(user, raw_token, 60 * 60 * 24 * 30)

    @app.post("/v1/auth/logout", status_code=204, tags=["auth"])
    def logout(
        request: Request,
        session: Session = Depends(get_session),
        authorization: str | None = Header(default=None),
    ) -> Response:
        user = authenticate_user(request, session, authorization)
        del user  # Authentication is intentionally required even for revocation.
        raw_token = session_token(authorization)
        if raw_token is not None:
            revoke_session(raw_token, session)
            session.commit()
        return Response(status_code=204)

    @app.get("/v1/me", tags=["auth"])
    def current_identity(tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        if tenant.api_key_id.startswith("session:"):
            user = session.get(User, tenant.api_key_id.removeprefix("session:"))
            if user is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            membership = session.scalar(select(Membership).where(Membership.user_id == user.id).order_by(Membership.created_at))
            return {"id": user.id, "user": _user_payload(user), "organizationId": membership.organization_id if membership else None, "authType": "session"}
        organization = session.get(Organization, tenant.organization_id)
        if organization is None:
            raise HTTPException(status_code=401, detail="Organization no longer exists")
        project = session.get(Project, tenant.project_id) if tenant.project_id else None
        if tenant.project_id and project is None:
            raise HTTPException(status_code=401, detail="Project no longer exists")
        result: dict[str, Any] = {
            "id": tenant.api_key_id,
            "organizationId": organization.id,
            "organizationName": organization.name,
            "projectId": project.id if project else None,
            "projectName": project.name if project else None,
            "authType": "demo" if tenant.api_key_id.startswith("demo:") else "api_key",
        }
        return result

    @app.patch("/v1/me", tags=["auth"])
    def update_identity(body: AuthProfilePatch, user: User = Depends(get_user), session: Session = Depends(get_session)) -> dict[str, Any]:
        user.name = body.name.strip()
        session.commit()
        return {"user": _user_payload(user)}

    @app.get("/v1/system/overview", tags=["system"])
    def system_overview(tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        """Return a tenant-scoped, authenticated view of backend capabilities.

        This endpoint is intentionally metadata-only. It helps the B2B studio
        and deployment smoke tests understand what the connected control plane
        can do without exposing provider credentials, prompts, completions, or
        other infrastructure secrets. Counts are queried from the database on
        every request, so this response is not a cached health claim.
        """
        organization = session.get(Organization, tenant.organization_id)
        if organization is None:
            raise HTTPException(status_code=401, detail="Organization no longer exists")

        project_query = select(Project).where(Project.organization_id == tenant.organization_id)
        if tenant.project_id:
            project_query = project_query.where(Project.id == tenant.project_id)
        projects = list(session.scalars(project_query.order_by(Project.created_at)).all())
        project_ids = [project.id for project in projects]

        trace_count = 0
        version_count = 0
        if project_ids:
            trace_count = int(
                session.scalar(
                    select(func.count(Trace.id)).where(
                        Trace.organization_id == tenant.organization_id,
                        Trace.project_id.in_(project_ids),
                    )
                )
                or 0
            )
            version_count = int(
                session.scalar(
                    select(func.count(ProjectVersion.id)).where(
                        ProjectVersion.organization_id == tenant.organization_id,
                        ProjectVersion.project_id.in_(project_ids),
                    )
                )
                or 0
            )

        return {
            "service": {
                "name": app.title,
                "version": app.version,
                "environment": os.getenv("ENVIRONMENT") or os.getenv("APP_ENV", "development"),
            },
            "scope": {
                "organizationId": organization.id,
                "projectId": tenant.project_id,
            },
            "organization": {"id": organization.id, "name": organization.name},
            "summary": {
                "projectCount": len(projects),
                "versionCount": version_count,
                "traceCount": trace_count,
            },
            "projects": [
                {"id": project.id, "name": project.name, "slug": project.slug}
                for project in projects
            ],
            "capabilities": {
                "otlpIngestion": True,
                "profiling": True,
                "evaluations": True,
                "optimization": True,
                "recommendationExport": True,
                "promptOutputStorage": False,
                "demoAuth": _demo_enabled(),
            },
            "dependencies": {
                "database": "ready",
                "queuePublisherConfigured": app.state.queue_publisher is not None,
            },
            "generatedAt": datetime.now(timezone.utc).isoformat(),
        }

    @app.post("/v1/auth/demo", tags=["auth"])
    def demo_sign_in() -> dict[str, Any]:
        """Issue a short-lived token for the explicitly enabled test workspace.

        This endpoint is intentionally unavailable when APP_ENV is production.
        It lets the static demo frontend obtain a token at runtime without
        embedding a bearer credential in its public JavaScript bundle.
        """
        if not _demo_enabled():
            raise HTTPException(status_code=404, detail="Demo authentication is disabled")
        organization_id, project_id = _configured_demo_tenant()
        if not organization_id:
            raise HTTPException(status_code=503, detail="Demo authentication is not configured")
        token = issue_demo_token(organization_id=organization_id, project_id=project_id, expires_in_seconds=3600)
        return {"accessToken": token, "tokenType": "Bearer", "expiresIn": 3600}

    @app.get("/v1/organizations/me", tags=["organizations"])
    def current_organization(tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        organization = session.get(Organization, tenant.organization_id)
        if organization is None: raise HTTPException(status_code=401, detail="Organization no longer exists")
        return {"id": organization.id, "name": organization.name}

    @app.get("/v1/projects", response_model=list[ProjectResponse], tags=["projects"])
    def list_projects(tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)):
        query = select(Project).where(Project.organization_id == tenant.organization_id)
        if tenant.project_id: query = query.where(Project.id == tenant.project_id)
        return session.scalars(query.order_by(Project.created_at)).all()

    @app.post("/v1/projects", response_model=ProjectResponse, status_code=201, tags=["projects"])
    def create_project(body: ProjectCreate, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)):
        if tenant.project_id: raise HTTPException(status_code=403, detail="Project-scoped API keys cannot create projects")
        _enforce_quota(tenant.organization_id, "projects", 1, session)
        project = Project(organization_id=tenant.organization_id, name=body.name, slug=body.slug)
        session.add(project)
        try: session.commit()
        except Exception:
            session.rollback(); raise HTTPException(status_code=409, detail="Project slug already exists")
        session.refresh(project); return project

    @app.get("/v1/projects/{project_id}/onboarding", tags=["projects"])
    def project_onboarding(project_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        """Return durable setup progress derived from persisted project evidence."""
        project = _project_reference(project_id, tenant=tenant, session=session)
        version = _latest_version(project, session)
        trace_count = int(session.scalar(select(func.count()).select_from(Trace).where(Trace.project_id == project.id)) or 0)
        dataset_cls = _model("EvalDataset")
        eval_count = int(session.scalar(select(func.count()).select_from(dataset_cls).where(dataset_cls.project_id == project.id)) or 0)
        baseline_count = int(session.scalar(select(func.count()).select_from(Job).where(Job.project_id == project.id, Job.kind == "baseline", Job.status == "completed")) or 0)
        completed = {
            "project": True,
            "agentVersion": version is not None,
            "traces": trace_count > 0,
            "evaluations": eval_count > 0,
            "baseline": baseline_count > 0,
        }
        if version is None:
            stage, next_action = "PROJECT_CREATED", "DEFINE_VERSION"
        elif trace_count == 0:
            stage, next_action = "VERSION_DEFINED", "CONNECT_AGENT"
        elif eval_count == 0:
            stage, next_action = "TRACES_OBSERVED", "IMPORT_EVALS"
        elif baseline_count == 0:
            stage, next_action = "EVALS_READY", "RUN_BASELINE"
        else:
            stage, next_action = "READY_TO_OPTIMIZE", "RUN_OPTIMIZATION"
        return {
            "projectId": project.id,
            "stage": stage,
            "completed": completed,
            "nextAction": next_action,
            "counts": {"versions": int(bool(version)), "traces": trace_count, "evalSuites": eval_count, "baselineRuns": baseline_count},
        }

    def _serialize_eval_suite(dataset: Any, *, include_cases: bool = False) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": dataset.id,
            "projectId": dataset.project_id,
            "organizationId": dataset.organization_id,
            "name": dataset.name,
            "version": dataset.version,
            "metadata": dataset.metadata_json or {},
            "caseCount": len(dataset.cases),
            "graderCount": len(dataset.graders),
            "createdAt": dataset.created_at.isoformat(),
            "updatedAt": dataset.updated_at.isoformat(),
        }
        if include_cases:
            include_content = os.getenv("AGENTPGO_STORE_CONTENT", "").lower() in {"1", "true", "yes"}
            result["cases"] = [
                {
                    "id": case.case_id,
                    "metadata": case.metadata_json or {},
                    **({"input": case.input_data, "expected": case.expected} if include_content else {}),
                }
                for case in dataset.cases
            ]
            result["graders"] = [{"name": grader.name, "kind": grader.kind, "config": grader.config} for grader in dataset.graders]
        return result

    def _eval_suite_for_tenant(suite_id: str, *, tenant: Tenant, session: Session) -> Any:
        dataset_cls = _model("EvalDataset")
        dataset = session.scalar(select(dataset_cls).where(dataset_cls.id == suite_id, dataset_cls.organization_id == tenant.organization_id))
        if dataset is None or (tenant.project_id and dataset.project_id != tenant.project_id):
            raise HTTPException(status_code=404, detail="Evaluation suite not found")
        return dataset

    def _eval_run_for_tenant(run_id: str, *, tenant: Tenant, session: Session) -> Any:
        run_cls = _model("EvalRun")
        run = session.scalar(select(run_cls).where(run_cls.id == run_id, run_cls.organization_id == tenant.organization_id))
        if run is None or (tenant.project_id and run.project_id != tenant.project_id):
            raise HTTPException(status_code=404, detail="Evaluation run not found")
        return run

    def _serialize_eval_run(run: Any) -> dict[str, Any]:
        return {
            "runId": run.id,
            "projectId": run.project_id,
            "evalSuiteId": run.eval_suite_id,
            "projectVersionId": run.project_version_id,
            "status": str(run.status or "queued").upper(),
            "candidateConfig": run.candidate_config or {},
            "graderSnapshot": run.grader_snapshot or [],
            "metrics": run.aggregate_metrics or {},
            "caseCount": len(run.cases),
            "completedCaseCount": sum(1 for case in run.cases if str(case.status).lower() in {"completed", "passed", "failed"}),
            "error": run.error,
            "createdAt": run.created_at.isoformat(),
            "updatedAt": run.updated_at.isoformat(),
            "startedAt": run.started_at.isoformat() if run.started_at else None,
            "completedAt": run.completed_at.isoformat() if run.completed_at else None,
        }

    @app.get("/v1/projects/{project_id}/eval-suites", response_model=dict[str, Any], tags=["evals"])
    def list_eval_suites(project_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        project = _project_reference(project_id, tenant=tenant, session=session)
        dataset_cls = _model("EvalDataset")
        suites = session.scalars(select(dataset_cls).where(dataset_cls.project_id == project.id, dataset_cls.organization_id == tenant.organization_id).order_by(desc(dataset_cls.created_at))).all()
        return {"data": [_serialize_eval_suite(suite) for suite in suites], "page": {"nextCursor": None}}

    @app.post("/v1/projects/{project_id}/eval-suites", response_model=dict[str, Any], status_code=201, tags=["evals"])
    def create_eval_suite(project_id: str, body: EvalSuiteCreate, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        project = _project_reference(project_id, tenant=tenant, session=session)
        dataset = _create_dataset(EvalCreate(name=body.name, cases=body.cases, graders=body.graders), tenant=tenant, project=project, session=session, metadata=body.metadata)
        return _serialize_eval_suite(session.get(_model("EvalDataset"), dataset["id"]), include_cases=True)

    @app.get("/v1/eval-suites/{suite_id}", response_model=dict[str, Any], tags=["evals"])
    def eval_suite_detail(suite_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        return _serialize_eval_suite(_eval_suite_for_tenant(suite_id, tenant=tenant, session=session), include_cases=True)

    @app.post("/v1/eval-suites/{suite_id}/runs", response_model=dict[str, Any], status_code=202, tags=["evals"])
    def start_eval_run(suite_id: str, body: EvalRunCreate, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        dataset = _eval_suite_for_tenant(suite_id, tenant=tenant, session=session)
        project = _project_reference(str(dataset.project_id), tenant=tenant, session=session)
        version = None
        if body.project_version_id:
            # Browser clients may retain the human version label (for example
            # ``v1``) while the run foreign key requires the immutable UUID.
            version = _version_for_run(project, body.project_version_id, tenant, session)
        selected_cases = list(dataset.cases[: body.sample_count] if body.sample_count else dataset.cases)
        if not selected_cases:
            raise HTTPException(status_code=422, detail="Evaluation suite must contain at least one case")
        run_cls, case_cls = _model("EvalRun"), _model("EvalRunCase")
        run_id = str(uuid4())
        grader_snapshot = [{"name": grader.name, "kind": grader.kind, "config": grader.config} for grader in dataset.graders]
        run = run_cls(id=run_id, organization_id=tenant.organization_id, project_id=project.id, eval_suite_id=dataset.id, project_version_id=version.id if version else None, status="queued", candidate_config=body.candidate_config, grader_snapshot=grader_snapshot)
        run.cases = [case_cls(case_id=case.case_id, ordinal=index) for index, case in enumerate(selected_cases)]
        session.add(run)
        # Keep both names while workers migrate: eval_suite_id is canonical,
        # dataset_id is the existing optimizer/evaluator linkage.
        job_payload = {"job_id": run_id, "kind": "evaluation", "organization_id": tenant.organization_id, "project_id": project.id, "eval_run_id": run_id, "eval_suite_id": dataset.id, "dataset_id": dataset.id, "config": {"dataset_id": dataset.id, "eval_suite_id": dataset.id, **body.candidate_config}}
        session.add(Job(id=run_id, organization_id=tenant.organization_id, project_id=project.id, kind="evaluation", payload=job_payload))
        _record_outbox(session, tenant, "eval_run", run_id, "eval_run.queued", {"job_id": run_id, "eval_suite_id": dataset.id})
        session.commit()
        if app.state.queue_publisher is not None:
            app.state.queue_publisher.publish(run_id, {"kind": "evaluation"})
        session.refresh(run)
        return _serialize_eval_run(run)

    @app.get("/v1/eval-runs/{run_id}", response_model=dict[str, Any], tags=["evals"])
    def eval_run_status(run_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        return _serialize_eval_run(_eval_run_for_tenant(run_id, tenant=tenant, session=session))

    @app.get("/v1/projects/{project_id}/eval-runs", response_model=dict[str, Any], tags=["evals"])
    def list_project_eval_runs(
        project_id: str,
        limit: int = Query(default=50, ge=1, le=100),
        tenant: Tenant = Depends(get_tenant),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        project = _project_reference(project_id, tenant=tenant, session=session)
        run_cls = _model("EvalRun")
        runs = session.scalars(
            select(run_cls)
            .where(
                run_cls.organization_id == tenant.organization_id,
                run_cls.project_id == project.id,
            )
            .order_by(desc(run_cls.created_at), desc(run_cls.id))
            .limit(limit)
        ).all()
        return {"data": [_serialize_eval_run(run) for run in runs], "page": {"nextCursor": None}}

    @app.get("/v1/eval-runs/{run_id}/cases", response_model=dict[str, Any], tags=["evals"])
    def eval_run_cases(run_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session), limit: int = Query(default=100, ge=1, le=1000)) -> dict[str, Any]:
        run_cls = _model("EvalRun")
        run = session.scalar(select(run_cls).where(run_cls.id == run_id, run_cls.organization_id == tenant.organization_id))
        if run is not None and (not tenant.project_id or run.project_id == tenant.project_id):
            rows = list(run.cases)[:limit]
            cases = []
            for case in rows:
                evidence = case.evidence if isinstance(case.evidence, dict) else {}
                cases.append({"id": case.id, "caseId": case.case_id, "ordinal": case.ordinal, "status": str(case.status).upper(), "passed": case.passed, "score": case.score, "latencyMs": case.latency_ms, "evidence": evidence, "baselineScore": evidence.get("baselineScore"), "optimizedScore": evidence.get("optimizedScore"), "baselineLatencyMs": evidence.get("baselineLatencyMs"), "optimizedLatencyMs": evidence.get("optimizedLatencyMs")})
        else:
            # Compatibility for the earlier optimization-run case view.  It
            # shares this URL, so retain its persisted dataset projection
            # while canonical EvalRun records use the normalized rows above.
            legacy = _run_for_tenant(run_id, "optimization", tenant, session)
            dataset_id = (legacy.get("config") or {}).get("dataset_id") or (legacy.get("config") or {}).get("datasetId")
            if not dataset_id:
                return {"data": [], "cases": [], "page": {"nextCursor": None}}
            dataset = _dataset_for_request(str(dataset_id), tenant=tenant, project=session.get(Project, legacy["project_id"]), session=session)
            cases = []
            for case in list(dataset.cases)[:limit]:
                metadata = case.metadata_json if isinstance(case.metadata_json, dict) else {}
                prompt = case.input_data if os.getenv("AGENTPGO_STORE_CONTENT", "").lower() in {"1", "true", "yes"} else None
                cases.append({"id": case.case_id, "category": str(metadata.get("category", "default")), "prompt": prompt, "baselineScore": float(metadata.get("baselineScore", 0)), "optimizedScore": float(metadata.get("optimizedScore", 0)), "baselineLatencyMs": float(metadata.get("baselineLatencyMs", 0)), "optimizedLatencyMs": float(metadata.get("optimizedLatencyMs", 0)), "status": str(metadata.get("status", "PASS")).upper(), "passed": bool(metadata.get("passed", True)), "diffNote": str(metadata.get("diffNote", ""))})
        # Keep `cases` for the current studio client and `data` for the
        # canonical collection envelope.
        return {"data": cases, "cases": cases, "page": {"nextCursor": None}}

    @app.post("/v1/projects/{project_id}/api-keys", status_code=201, tags=["auth"])
    def create_project_api_key(project_id: str, body: ProjectApiKeyCreate, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        if tenant.project_id:
            raise HTTPException(status_code=403, detail="Project-scoped credentials cannot create API keys")
        project = _project_reference(project_id, tenant=tenant, session=session)
        secret, key = issue_api_key(organization_id=tenant.organization_id, project_id=project.id, name=body.name.strip())
        session.add(key)
        session.commit()
        session.refresh(key)
        return {"id": key.id, "name": key.name, "secret": secret, "keyPrefix": key.key_prefix, "projectId": project.id, "createdAt": key.created_at.isoformat()}

    @app.get("/v1/projects/{project_id}/api-keys", tags=["auth"])
    def list_project_api_keys(project_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> list[dict[str, Any]]:
        if tenant.project_id:
            raise HTTPException(status_code=403, detail="Project-scoped credentials cannot list API keys")
        project = _project_reference(project_id, tenant=tenant, session=session)
        keys = session.scalars(select(api_models.ApiKey).where(api_models.ApiKey.organization_id == tenant.organization_id, api_models.ApiKey.project_id == project.id).order_by(api_models.ApiKey.created_at)).all()
        return [{"id": key.id, "name": key.name, "keyPrefix": key.key_prefix, "projectId": project.id, "revoked": key.revoked_at is not None, "createdAt": key.created_at.isoformat(), "revokedAt": key.revoked_at.isoformat() if key.revoked_at else None} for key in keys]

    @app.post("/v1/projects/{project_id}/api-keys/{key_id}/revoke", tags=["auth"])
    def revoke_project_api_key(project_id: str, key_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        if tenant.project_id:
            raise HTTPException(status_code=403, detail="Project-scoped credentials cannot revoke API keys")
        project = _project_reference(project_id, tenant=tenant, session=session)
        key = session.scalar(select(api_models.ApiKey).where(api_models.ApiKey.id == key_id, api_models.ApiKey.organization_id == tenant.organization_id, api_models.ApiKey.project_id == project.id))
        if key is None:
            raise HTTPException(status_code=404, detail="API key not found")
        revoke_api_key(key)
        session.commit()
        return {"id": key.id, "revoked": True, "revokedAt": key.revoked_at.isoformat() if key.revoked_at else None}

    def _project_version(project_id: str, version_id: str | None, tenant: Tenant, session: Session) -> ProjectVersion:
        project = _project_reference(project_id, tenant=tenant, session=session)
        query = select(ProjectVersion).where(
            ProjectVersion.project_id == project.id,
            ProjectVersion.organization_id == tenant.organization_id,
        )
        if version_id:
            query = query.where(ProjectVersion.id == version_id)
        else:
            query = query.order_by(desc(ProjectVersion.created_at))
        version = session.scalar(query)
        if version is None:
            raise HTTPException(status_code=404, detail="Project version not found")
        return version

    def _latest_version(project: Project, session: Session) -> ProjectVersion | None:
        return session.scalar(
            select(ProjectVersion)
            .where(ProjectVersion.project_id == project.id, ProjectVersion.organization_id == project.organization_id)
            .order_by(desc(ProjectVersion.created_at))
        )

    def _metric(metrics: dict[str, Any], *names: str, default: Any = 0) -> Any:
        for name in names:
            if name in metrics:
                return metrics[name]
        return default

    def _canonical_model(value: Any, field_name: str) -> str:
        model = str(value or "").strip()
        if not model or "/" not in model or len(model) > 255:
            raise HTTPException(status_code=422, detail=f"{field_name} must be a canonical provider/model identifier")
        return model

    @app.get("/v1/projects/{project_id}", response_model=dict[str, Any], tags=["projects"])
    def project_detail(project_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        project = _project_reference(project_id, tenant=tenant, session=session)
        return serialize_project(project, _latest_version(project, session))

    @app.get("/v1/projects/{project_id}/versions", response_model=dict[str, Any], tags=["projects"])
    def project_versions(project_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        project = _project_reference(project_id, tenant=tenant, session=session)
        versions = session.scalars(
            select(ProjectVersion).where(ProjectVersion.project_id == project.id).order_by(desc(ProjectVersion.created_at))
        ).all()
        return {"data": [serialize_version(version) for version in versions], "page": {"nextCursor": None}}

    @app.post("/v1/projects/{project_id}/versions", response_model=dict[str, Any], status_code=201, tags=["projects"])
    def create_project_version(project_id: str, body: VersionCreate, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        project = _project_reference(project_id, tenant=tenant, session=session)
        try:
            validate_graph(body.nodes, body.edges)
        except GraphValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        node_ids = {str(item.get("nodeId", item.get("id", ""))) for item in body.nodes}
        nodes: list[api_models.AgentNode] = []
        for ordinal, item in enumerate(body.nodes):
            node_id = str(item.get("nodeId", item.get("id", ""))).strip()
            baseline_model = _canonical_model(item.get("baselineModel", item.get("baseline_model")), "baselineModel")
            current_model = _canonical_model(item.get("currentModel", item.get("current_model", baseline_model)), "currentModel")
            optimized_model = _canonical_model(item.get("optimizedModel", item.get("optimized_model", current_model)), "optimizedModel")
            candidates = item.get("candidates", [])
            if not isinstance(candidates, list):
                raise HTTPException(status_code=422, detail="node candidates must be an array")
            nodes.append(api_models.AgentNode(
                organization_id=tenant.organization_id, project_id=project.id, node_id=node_id,
                name=str(item.get("name", node_id))[:255], role=str(item.get("role", ""))[:255],
                x=float(item.get("x", 0)), y=float(item.get("y", 0)), baseline_model=baseline_model,
                current_model=current_model, optimized_model=optimized_model, calls=int(item.get("calls", 0)),
                avg_cost=_metric(item, "avgCost", "avg_cost"), baseline_cost=_metric(item, "baselineCost", "baseline_cost"),
                optimized_cost=_metric(item, "optimizedCost", "optimized_cost"), latency_sec=_metric(item, "latencySec", "latency_sec"),
                baseline_latency_sec=_metric(item, "baselineLatencySec", "baseline_latency_sec"), optimized_latency_sec=_metric(item, "optimizedLatencySec", "optimized_latency_sec"),
                input_tokens=int(item.get("inputTokens", item.get("input_tokens", 0))), output_tokens=int(item.get("outputTokens", item.get("output_tokens", 0))),
                cost_share_pct=_metric(item, "costSharePct", "cost_share_pct"), quality_sensitivity=str(item.get("qualitySensitivity", item.get("quality_sensitivity", "MEDIUM"))).upper(),
                is_hotspot=bool(item.get("isHotspot", item.get("is_hotspot", False))), candidates=candidates, ordinal=ordinal,
            ))
        version = ProjectVersion(
            organization_id=tenant.organization_id, project_id=project.id, version=body.version,
            environment=body.environment.upper(), run_id=body.run_id, nodes=nodes,
            total_executions=int(_metric(body.metrics, "totalExecutions", "total_executions")),
            baseline_cost=_metric(body.metrics, "baselineCost", "baseline_cost"), optimized_cost=_metric(body.metrics, "optimizedCost", "optimized_cost"),
            savings_pct=_metric(body.metrics, "savingsPct", "savings_pct"), monthly_savings_estimate=_metric(body.metrics, "monthlySavingsEstimate", "monthly_savings_estimate"),
            monthly_requests=int(_metric(body.metrics, "monthlyRequests", "monthly_requests")), baseline_latency_p95=_metric(body.metrics, "baselineLatencyP95", "baseline_latency_p95"),
            optimized_latency_p95=_metric(body.metrics, "optimizedLatencyP95", "optimized_latency_p95"), baseline_quality=_metric(body.metrics, "baselineQuality", "baseline_quality"),
            optimized_quality=_metric(body.metrics, "optimizedQuality", "optimized_quality"), eval_cases_count=int(_metric(body.metrics, "evalCasesCount", "eval_cases_count")),
            quality_tolerance_pct=_metric(body.metrics, "qualityTolerancePct", "quality_tolerance_pct", default=1), confidence_pct=_metric(body.metrics, "confidencePct", "confidence_pct", default=95),
        )
        version.edges = [api_models.GraphEdge(
            organization_id=tenant.organization_id, project_id=project.id,
            edge_id=str(item.get("edgeId", item.get("id", f"edge-{ordinal}"))),
            from_node=str(item.get("from", item.get("fromNode", item.get("from_node", "")))),
            to_node=str(item.get("to", item.get("toNode", item.get("to_node", "")))),
            label=item.get("label"), throughput_tokens_per_sec=_metric(item, "throughputTokensPerSec", "throughput_tokens_per_sec"),
            avg_latency_ms=_metric(item, "avgLatencyMs", "avg_latency_ms"), ordinal=ordinal,
        ) for ordinal, item in enumerate(body.edges)]
        session.add(version)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail="Project version already exists") from exc
        session.refresh(version)
        return serialize_version(version)

    @app.get("/v1/projects/{project_id}/versions/{version_id}", response_model=dict[str, Any], tags=["projects"])
    def project_version_detail(project_id: str, version_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        return serialize_version(_project_version(project_id, version_id, tenant, session))

    def _settings(project: Project, tenant: Tenant, session: Session) -> ProjectSettings:
        settings = session.scalar(select(ProjectSettings).where(ProjectSettings.project_id == project.id, ProjectSettings.organization_id == tenant.organization_id))
        if settings is None:
            settings = ProjectSettings(organization_id=tenant.organization_id, project_id=project.id)
            session.add(settings)
            session.commit()
            session.refresh(settings)
        return settings

    @app.get("/v1/projects/{project_id}/settings", response_model=dict[str, Any], tags=["projects"])
    def get_project_settings(project_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        project = _project_reference(project_id, tenant=tenant, session=session)
        return serialize_settings(_settings(project, tenant, session))

    @app.patch("/v1/projects/{project_id}/settings", response_model=dict[str, Any], tags=["projects"])
    def patch_project_settings(project_id: str, body: ProjectSettingsPatch, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        project = _project_reference(project_id, tenant=tenant, session=session)
        if body.quality_tolerance_pp is not None and body.quality_tolerance_pct is not None and body.quality_tolerance_pp != body.quality_tolerance_pct:
            raise HTTPException(status_code=422, detail="quality tolerance aliases must match")
        settings = _settings(project, tenant, session)
        tolerance = body.quality_tolerance_pp if body.quality_tolerance_pp is not None else body.quality_tolerance_pct
        if tolerance is not None:
            settings.quality_tolerance_pct = tolerance
        for field_name in ("confidence_pct", "max_p95_latency_ms", "objective", "allowed_models"):
            value = getattr(body, field_name)
            if value is not None:
                setattr(settings, field_name, value)
        session.commit()
        session.refresh(settings)
        return serialize_settings(settings)

    def _if_match_revision(value: str | None) -> int | None:
        if not value:
            return None
        candidate = value.strip().removeprefix("W/").strip('"')
        try:
            return int(candidate)
        except ValueError:
            raise HTTPException(status_code=422, detail="If-Match must contain a numeric layout revision")

    @app.get("/v1/projects/{project_id}/layout", response_model=dict[str, Any], tags=["projects"])
    def get_project_layout(project_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        project = _project_reference(project_id, tenant=tenant, session=session)
        layout = session.scalar(select(ProjectLayout).where(ProjectLayout.project_id == project.id).order_by(desc(ProjectLayout.revision)))
        return serialize_layout(layout, project.id)

    @app.post("/v1/projects/{project_id}/layout", response_model=dict[str, Any], tags=["projects"])
    def write_project_layout(project_id: str, body: LayoutWrite, request: Request, if_match: str | None = Header(default=None), tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        project = _project_reference(project_id, tenant=tenant, session=session)
        header_revision = _if_match_revision(if_match)
        if body.revision is not None and header_revision is not None and body.revision != header_revision:
            raise HTTPException(status_code=409, detail="Layout revision conflict")
        supplied_revision = body.revision if body.revision is not None else header_revision
        if supplied_revision is None:
            raise HTTPException(status_code=409, detail="Layout revision or If-Match is required")
        current = session.scalar(select(ProjectLayout).where(ProjectLayout.project_id == project.id).order_by(desc(ProjectLayout.revision)))
        current_revision = current.revision if current else 0
        if supplied_revision != current_revision:
            raise HTTPException(status_code=409, detail="Layout revision conflict")
        version_id = body.version_id
        if version_id:
            _project_version(project.id, version_id, tenant, session)
        layout = ProjectLayout(organization_id=tenant.organization_id, project_id=project.id, version_id=version_id, revision=current_revision + 1, nodes=body.nodes)
        session.add(layout)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(status_code=409, detail="Layout revision conflict") from exc
        session.refresh(layout)
        response = serialize_layout(layout, project.id)
        response["revision"] = layout.revision
        return response

    @app.post("/v1/traces", response_model=IngestionResponse, tags=["otlp"], dependencies=[Depends(_require_json)])
    @app.post("/v1/otlp/v1/traces", response_model=IngestionResponse, include_in_schema=False, tags=["otlp"], dependencies=[Depends(_require_json)])
    def ingest_traces(request: Request, payload: OTLPExportRequest, project_id: str | None = Query(default=None), tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> IngestionResponse:
        project = _project_for_request(request=request, project_id=project_id, tenant=tenant, session=session)
        accepted, seen = 0, set()
        for span, resource_attributes, service_name, scope in _spans(payload):
            key = (span.trace_id.lower(), span.span_id.lower())
            if key in seen: continue
            seen.add(key)
            existing = session.scalar(select(Trace.id).where(Trace.project_id == project.id, Trace.trace_id == key[0], Trace.span_id == key[1]))
            if existing is not None: continue
            redacted_span = _redact(span.model_dump(by_alias=True, exclude_none=True))
            raw_span = redacted_span if os.getenv("AGENTPGO_STORE_RAW_SPAN", "").lower() in {"1", "true", "yes"} else {}
            session.add(Trace(organization_id=tenant.organization_id, project_id=project.id, trace_id=key[0], span_id=key[1], parent_span_id=span.parent_span_id.lower() if span.parent_span_id else None, name=span.name, kind=span.kind, start_time=_nanos_to_datetime(span.start_time_unix_nano), end_time=_nanos_to_datetime(span.end_time_unix_nano), status_code=span.status.code if span.status else None, status_message=REDACTED if span.status and span.status.message else None, service_name=service_name if isinstance(service_name, str) else None, resource_attributes=_redact(resource_attributes), attributes=_attributes(span.attributes), events=_redact(span.events), links=_redact(span.links), scope=scope, raw_span=raw_span))
            accepted += 1
        _enforce_quota(tenant.organization_id, "traceSpans", accepted, session)
        session.commit(); return IngestionResponse(accepted=accepted)

    @app.get("/v1/projects/{project_id}/profile", response_model=dict[str, Any], tags=["profile"])
    def project_profile(project_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        project = _project_reference(project_id, tenant=tenant, session=session)
        rows = list(session.scalars(select(Trace).where(Trace.project_id == project.id)).all())
        measurements = [trace_metrics(row) for row in rows]
        latencies = [float(item["durationMs"]) for item in measurements if item["startedAt"] and item["endedAt"]]
        p95 = quantiles(latencies, n=20, method="inclusive")[18] if len(latencies) >= 2 else (latencies[0] if latencies else 0.0)
        p50 = quantiles(latencies, n=2, method="inclusive")[0] if len(latencies) >= 2 else (latencies[0] if latencies else 0.0)
        traces_observed = len({item["traceId"] for item in measurements})
        total_cost = sum(float(item["cost"]) for item in measurements)
        total_input = sum(int(item["inputTokens"]) for item in measurements)
        total_output = sum(int(item["outputTokens"]) for item in measurements)
        error_count = sum(item["status"] == "error" for item in measurements)
        by_node: dict[str, dict[str, Any]] = {}
        by_model: dict[str, dict[str, Any]] = {}
        for item in measurements:
            for key, target in ((item["nodeId"] or "unknown", by_node), (item["model"] or "unknown", by_model)):
                bucket = target.setdefault(key, {"calls": 0, "inputTokens": 0, "outputTokens": 0, "cost": 0.0, "latencyMs": 0.0, "errorCount": 0})
                bucket["calls"] += 1
                bucket["inputTokens"] += item["inputTokens"]
                bucket["outputTokens"] += item["outputTokens"]
                bucket["cost"] += item["cost"]
                bucket["latencyMs"] += item["durationMs"]
                bucket["errorCount"] += item["status"] == "error"
        for bucket in (*by_node.values(), *by_model.values()):
            bucket["avgLatencyMs"] = bucket["latencyMs"] / bucket["calls"] if bucket["calls"] else 0.0
        return {
            "project_id": project.id,
            "runs_observed": traces_observed,
            "model_calls": len(rows),
            "p50_latency_ms": p50,
            "p95_latency_ms": p95,
            "spans": len(rows),
            "avg_latency_ms": sum(latencies) / len(latencies) if latencies else 0.0,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "total_cost_usd": total_cost,
            "cost_per_request_usd": total_cost / traces_observed if traces_observed else 0.0,
            "avg_cost_per_call_usd": total_cost / len(rows) if rows else 0.0,
            "error_count": error_count,
            "error_rate_pct": error_count / len(rows) * 100 if rows else 0.0,
            "by_node": by_node,
            "by_model": by_model,
        }

    @app.get("/v1/projects/{project_id}/traces", response_model=dict[str, Any], tags=["traces"])
    def list_project_traces(
        project_id: str,
        cursor: str | None = Query(default=None, max_length=512),
        limit: int = Query(default=50, ge=1, le=MAX_TRACE_PAGE_SIZE),
        tenant: Tenant = Depends(get_tenant),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        project = _project_reference(project_id, tenant=tenant, session=session)
        query = select(Trace).where(Trace.project_id == project.id, Trace.organization_id == tenant.organization_id)
        if cursor:
            try:
                received_at, row_id = decode_cursor(cursor)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="Invalid trace cursor") from exc
            query = query.where(
                or_(Trace.received_at < received_at, and_(Trace.received_at == received_at, Trace.id < row_id))
            )
        rows = list(session.scalars(query.order_by(desc(Trace.received_at), desc(Trace.id)).limit(limit + 1)).all())
        has_more = len(rows) > limit
        rows = rows[:limit]
        next_cursor = encode_cursor(rows[-1].received_at, rows[-1].id) if has_more and rows else None
        return {"data": [serialize_trace(row) for row in rows], "page": {"nextCursor": next_cursor}}

    @app.get("/v1/projects/{project_id}/traces/{trace_id}", response_model=dict[str, Any], tags=["traces"])
    def get_project_trace(trace_id: str, project_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        project = _project_reference(project_id, tenant=tenant, session=session)
        rows = list(session.scalars(select(Trace).where(Trace.project_id == project.id, Trace.organization_id == tenant.organization_id, Trace.trace_id == trace_id.lower()).order_by(Trace.start_time, Trace.id)).all())
        if not rows:
            raise HTTPException(status_code=404, detail="Trace not found")
        return serialize_trace_detail(rows[0].trace_id, rows, project.id)

    @app.post("/v1/profiles", response_model=dict[str, Any], status_code=202, tags=["profile"])
    def queue_profile(payload: dict[str, Any], request: Request, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        project = _project_for_request(request=request, project_id=payload.get("project_id"), tenant=tenant, session=session)
        run_id = str(uuid4()); job = Job(id=run_id, organization_id=tenant.organization_id, project_id=project.id, kind="profile", payload={**payload, "project_id": project.id})
        session.add(job); _record_outbox(session, tenant, "profile", run_id, "profile.queued", {"job_id": run_id}); session.commit()
        if app.state.queue_publisher is not None: app.state.queue_publisher.publish(run_id, {"kind": "profile"})
        return {"run_id": run_id, "status": "queued"}

    @app.get("/v1/profiles/{profile_id}", response_model=dict[str, Any], tags=["profile"])
    def get_profile(profile_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        """Read one durable profile job without crossing organization/project scope."""
        return _run_for_tenant(profile_id, "profile", tenant, session)

    @app.post("/v1/evals", response_model=dict[str, Any], status_code=201, tags=["evals"])
    def create_eval(body: EvalCreate, request: Request, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        project = _project_for_request(request=request, project_id=body.project_id, tenant=tenant, session=session)
        return _create_dataset(body, tenant=tenant, project=project, session=session)

    @app.post("/v1/evals/import", response_model=dict[str, Any], status_code=202, tags=["evals"])
    def import_eval(payload: dict[str, Any], request: Request, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        cases = payload.get("cases", [])
        graders = payload.get("graders", [])
        if not isinstance(cases, list) or len(cases) > MAX_EVAL_CASES: raise HTTPException(status_code=422, detail="invalid or oversized evaluation cases")
        if not isinstance(graders, list) or len(graders) > MAX_EVAL_GRADERS: raise HTTPException(status_code=422, detail="invalid or oversized evaluation graders")
        body = EvalCreate(project_id=payload.get("project_id"), name=str(payload.get("name", payload.get("project", "imported"))), cases=cases, graders=graders)
        project = _project_for_request(request=request, project_id=body.project_id, tenant=tenant, session=session)
        result = _create_dataset(body, tenant=tenant, project=project, session=session)
        return {"dataset_id": result["id"], "status": "accepted", "case_count": len(cases)}

    @app.get("/v1/evals/{dataset_id}", response_model=dict[str, Any], tags=["evals"])
    def get_eval(dataset_id: str, request: Request, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        cls = _model("EvalDataset"); dataset = session.scalar(select(cls).where(cls.id == dataset_id, cls.organization_id == tenant.organization_id))
        if dataset is None or (tenant.project_id and dataset.project_id != tenant.project_id): raise HTTPException(status_code=404, detail="Evaluation dataset not found")
        return {"id": dataset.id, "project_id": dataset.project_id, "organization_id": dataset.organization_id, "name": dataset.name, "version": dataset.version, "cases": [{"id": c.case_id, "input": c.input_data, "expected": c.expected, "metadata": c.metadata_json} for c in dataset.cases], "graders": [{"name": g.name, "kind": g.kind, "config": g.config} for g in dataset.graders]}

    @app.post("/v1/evals/{dataset_id}/cases", response_model=dict[str, Any], tags=["evals"])
    def add_eval_cases(dataset_id: str, cases: list[dict[str, Any]], request: Request, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        if len(cases) > MAX_EVAL_CASES: raise HTTPException(status_code=422, detail="too many evaluation cases")
        dataset_cls, case_cls = _model("EvalDataset"), _model("EvalCase")
        dataset = session.scalar(select(dataset_cls).where(dataset_cls.id == dataset_id, dataset_cls.organization_id == tenant.organization_id))
        if dataset is None or (tenant.project_id and dataset.project_id != tenant.project_id): raise HTTPException(status_code=404, detail="Evaluation dataset not found")
        _enforce_quota(tenant.organization_id, "evalCases", len(cases), session)
        current = len(dataset.cases); [session.add(case_cls(dataset_id=dataset.id, **_case_values(case, current + i))) for i, case in enumerate(cases)]
        dataset.version += 1; session.commit(); return {"id": dataset.id, "case_count": current + len(cases), "version": dataset.version}

    def _version_for_run(project: Project, reference: str | None, tenant: Tenant, session: Session) -> ProjectVersion | None:
        if not reference or reference == "latest":
            return _latest_version(project, session)
        version = session.scalar(select(ProjectVersion).where(
            ProjectVersion.organization_id == tenant.organization_id,
            ProjectVersion.project_id == project.id,
            or_(ProjectVersion.id == reference, ProjectVersion.version == reference),
        ))
        if version is None:
            raise HTTPException(status_code=404, detail="Project version not found")
        return version

    def _append_optimization_event(
        run_id: str,
        tenant: Tenant,
        project_id: str | None,
        event_type: str,
        payload: dict[str, Any],
        session: Session,
        *,
        event_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Append one replayable event; event IDs make retries harmless."""
        event_cls = getattr(api_models, "OptimizationEvent", None)
        if event_cls is None:
            return None
        stable_id = event_id or f"{run_id}:{uuid4().hex}"
        existing = session.scalar(select(event_cls).where(event_cls.job_id == run_id, event_cls.event_id == stable_id))
        if existing is not None:
            return {"id": existing.event_id, "sequence": existing.sequence, **(existing.payload or {})}
        max_sequence = session.scalar(select(func.max(event_cls.sequence)).where(event_cls.job_id == run_id))
        sequence = int(max_sequence or 0) + 1
        event = event_cls(
            organization_id=tenant.organization_id, project_id=project_id, job_id=run_id,
            sequence=sequence, event_id=stable_id, event_type=event_type.upper(), payload=dict(payload),
        )
        session.add(event)
        session.commit()
        return {"id": stable_id, "sequence": sequence, "type": event_type.upper(), "timestamp": event.created_at.isoformat(), **payload}

    def _run_payload(job: Job, *, include_events: bool = False) -> dict[str, Any]:
        payload = job.payload if isinstance(job.payload, dict) else {}
        result: dict[str, Any] = {
            "runId": job.id, "run_id": job.id, "organizationId": job.organization_id, "organization_id": job.organization_id,
            # Legacy /optimizations callers consume lower-case state values;
            # the frontend maps them to its display enum when needed.
            "projectId": job.project_id, "project_id": job.project_id, "kind": job.kind, "status": str(job.status).lower(),
            "projectVersionId": job.project_version_id or payload.get("project_version_id"),
            "datasetId": job.dataset_id or payload.get("dataset_id"),
            "evalSuiteId": job.dataset_id or payload.get("dataset_id"),
            "config": payload.get("config", {}), "candidates": payload.get("candidates", []),
            "maxExperimentCostUsd": job.max_experiment_cost_usd,
            "spentUsd": job.spent_usd, "objective": job.objective,
            "qualityTolerancePp": float(job.quality_tolerance_pp) if job.quality_tolerance_pp is not None else None,
            "confidencePct": float(job.confidence_pct) if job.confidence_pct is not None else None,
            "allowedModels": list(job.allowed_models or []), "cancelRequested": job.cancel_requested_at is not None,
            "result": job.result, "error": job.error,
            "createdAt": job.created_at.isoformat(), "updatedAt": job.updated_at.isoformat(),
            "startedAt": job.started_at.isoformat() if job.started_at else None,
            "completedAt": job.completed_at.isoformat() if job.completed_at else None,
        }
        if include_events:
            result["events"] = [
                {"id": event.event_id, "sequence": event.sequence, "type": event.event_type, "timestamp": event.created_at.isoformat(), **(event.payload or {})}
                for event in (job.optimization_events or [])
            ]
        return result

    def _queue_run(kind: str, body: RunCreate, request: Request, tenant: Tenant, session: Session) -> RunResponse:
        project = _project_for_request(request=request, project_id=body.project_id, tenant=tenant, session=session)
        if body.dataset_id: _dataset_for_request(body.dataset_id, tenant=tenant, project=project, session=session)
        version = _version_for_run(project, body.project_version_id, tenant, session)
        # The canonical API accepts the standard HTTP header as an equivalent
        # to the JSON field so SDKs can retry without mutating their payload.
        idempotency_key = body.idempotency_key or request.headers.get("Idempotency-Key")
        run_id = str(uuid4()); config = dict(body.config); config["max_experiment_cost_usd"] = body.max_experiment_cost_usd
        # Canonicalize the suite linkage inside config as well as the legacy
        # top-level payload field.  Consumers of the old browser route read
        # config.dataset_id, while workers read the top-level dataset_id.
        if body.dataset_id:
            config["dataset_id"] = body.dataset_id
        config["project_version_id"] = version.id if version else None
        payload_data = {"job_id": run_id, "kind": kind, "organization_id": tenant.organization_id, "project_id": project.id, "dataset_id": body.dataset_id, "config": config}
        if JobPayload is not None:
            try: payload = JobPayload.model_validate(payload_data).to_wire()
            except Exception as exc: raise HTTPException(status_code=422, detail=f"invalid run config: {exc}") from exc
        else: payload = {**payload_data, "candidates": config.get("candidates", [])}
        job = Job(
            id=run_id, organization_id=tenant.organization_id, project_id=project.id, kind=kind,
            payload=payload, max_experiment_cost_usd=body.max_experiment_cost_usd,
            project_version_id=version.id if version else None, dataset_id=body.dataset_id,
            idempotency_key=idempotency_key, objective=body.objective,
            quality_tolerance_pp=body.quality_tolerance_pp, confidence_pct=body.confidence_pct,
            allowed_models=list(body.allowed_models or []),
        )
        idem_cls = getattr(api_models, "OptimizationIdempotency", None)
        request_hash = hashlib.sha256(json.dumps({
            "kind": kind, "projectId": project.id, "projectVersionId": version.id if version else None,
            "datasetId": body.dataset_id, "objective": body.objective,
            "qualityTolerancePp": body.quality_tolerance_pp, "confidencePct": body.confidence_pct,
            "allowedModels": body.allowed_models, "config": config,
            "maxExperimentCostUsd": body.max_experiment_cost_usd,
        }, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
        if idem_cls is not None and idempotency_key:
            existing = session.scalar(select(idem_cls).where(
                idem_cls.organization_id == tenant.organization_id,
                idem_cls.operation == kind,
                idem_cls.idempotency_key == idempotency_key,
            ))
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise HTTPException(status_code=409, detail="Idempotency key was already used with a different request")
                existing_job = session.get(Job, existing.job_id)
                if existing_job is not None:
                    return RunResponse(run_id=existing_job.id, status=existing_job.status)
        if kind == "optimization":
            _enforce_quota(tenant.organization_id, "optimizationRuns", 1, session)
            snapshot = _entitlement_snapshot(tenant.organization_id, session)
            if body.max_experiment_cost_usd > float(snapshot["limits"]["maxExperimentCostUsd"]):
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": "ENTITLEMENT_LIMIT_REACHED",
                        "message": "Requested experiment budget exceeds the plan limit",
                        "fields": {"plan": snapshot["plan"], "limit": "maxExperimentCostUsd", "requested": body.max_experiment_cost_usd, "maximum": snapshot["limits"]["maxExperimentCostUsd"]},
                    },
                )
        session.add(job)
        if idem_cls is not None and idempotency_key:
            session.add(idem_cls(
                organization_id=tenant.organization_id, project_id=project.id, operation=kind,
                idempotency_key=idempotency_key, request_hash=request_hash, job_id=run_id,
            ))
        _record_outbox(session, tenant, kind, run_id, f"{kind}.queued", {"job_id": run_id})
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            if idem_cls is not None and idempotency_key:
                existing = session.scalar(select(idem_cls).where(
                    idem_cls.organization_id == tenant.organization_id,
                    idem_cls.operation == kind,
                    idem_cls.idempotency_key == idempotency_key,
                ))
                if existing is not None:
                    if existing.request_hash != request_hash:
                        raise HTTPException(status_code=409, detail="Idempotency key was already used with a different request") from exc
                    existing_job = session.get(Job, existing.job_id)
                    if existing_job is not None:
                        return RunResponse(run_id=existing_job.id, status=existing_job.status)
            raise HTTPException(status_code=409, detail="Optimization request conflicts with an existing run") from exc
        if app.state.queue_publisher is not None: app.state.queue_publisher.publish(run_id, {"kind": kind})
        _append_optimization_event(run_id, tenant, project.id, "INFO", {"message": f"{kind} queued", "status": "QUEUED"}, session)
        return RunResponse(run_id=run_id, status="queued")

    @app.post("/v1/baselines/run", response_model=RunResponse, status_code=202, tags=["baselines"])
    def run_baseline(body: RunCreate, request: Request, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> RunResponse: return _queue_run("baseline", body, request, tenant, session)

    @app.post("/v1/optimize", response_model=RunResponse, status_code=202, include_in_schema=False, tags=["optimizations"])
    def cli_optimize(body: RunCreate, request: Request, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> RunResponse: return _queue_run("optimization", body, request, tenant, session)

    @app.post("/v1/optimizations", response_model=RunResponse, status_code=202, tags=["optimizations"])
    def create_optimization(body: RunCreate, request: Request, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> RunResponse: return _queue_run("optimization", body, request, tenant, session)

    @app.post("/v1/projects/{project_id}/optimization-runs", response_model=dict[str, Any], status_code=202, tags=["optimizations"])
    def start_frontend_optimization(project_id: str, payload: dict[str, Any], request: Request, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        """Accept the browser contract while retaining the canonical run payload."""
        raw_budget = payload.get("maxExperimentCostUsd", payload.get("max_experiment_cost_usd", 25.0))
        try:
            budget = float(raw_budget)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="maxExperimentCostUsd must be numeric") from exc
        config = payload.get("config") if isinstance(payload.get("config"), dict) else dict(payload)
        config.pop("projectVersionId", None)
        config.pop("evalSuiteId", None)
        body = RunCreate(
            project_id=project_id,
            dataset_id=payload.get("evalSuiteId") or payload.get("datasetId"),
            project_version_id=payload.get("projectVersionId") or payload.get("project_version_id"),
            idempotency_key=payload.get("idempotencyKey") or payload.get("idempotency_key") or request.headers.get("Idempotency-Key"),
            objective=str(payload.get("objective")) if payload.get("objective") is not None else None,
            quality_tolerance_pp=payload.get("qualityTolerancePp", payload.get("quality_tolerance_pp")),
            confidence_pct=payload.get("confidencePct", payload.get("confidence_pct")),
            allowed_models=payload.get("allowedModels", payload.get("allowed_models", [])) or [],
            config=config,
            max_experiment_cost_usd=budget,
        )
        result = _queue_run("optimization", body, request, tenant, session)
        return {"runId": result.run_id, "status": result.status, "createdAt": datetime.now(timezone.utc).isoformat()}

    def _run_for_tenant(run_id: str, kind: str, tenant: Tenant, session: Session) -> dict[str, Any]:
        query = select(Job).where(Job.id == run_id, Job.organization_id == tenant.organization_id, Job.kind == kind)
        if tenant.project_id: query = query.where(Job.project_id == tenant.project_id)
        job = session.scalar(query)
        if job is None: raise HTTPException(status_code=404, detail=f"{kind.title()} not found")
        payload = job.payload if isinstance(job.payload, dict) else {}
        return _run_payload(job)

    @app.get("/v1/baselines/{run_id}", response_model=dict[str, Any], tags=["baselines"])
    def get_baseline(run_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)): return _run_for_tenant(run_id, "baseline", tenant, session)

    @app.get("/v1/optimizations/{run_id}", response_model=dict[str, Any], tags=["optimizations"])
    def get_optimization(run_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)): return _run_for_tenant(run_id, "optimization", tenant, session)

    @app.get("/v1/projects/{project_id}/optimization-runs", response_model=dict[str, Any], tags=["optimizations"])
    def list_optimization_runs(
        project_id: str,
        limit: int = Query(default=50, ge=1, le=100),
        tenant: Tenant = Depends(get_tenant),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        project = _project_reference(project_id, tenant=tenant, session=session)
        jobs = session.scalars(
            select(Job).where(
                Job.organization_id == tenant.organization_id,
                Job.project_id == project.id,
                Job.kind == "optimization",
            ).order_by(desc(Job.created_at)).limit(limit)
        ).all()
        return {"data": [_run_payload(job) for job in jobs], "runs": [_run_payload(job) for job in jobs], "page": {"nextCursor": None}}

    @app.get("/v1/optimization-runs/{run_id}", response_model=dict[str, Any], tags=["optimizations"])
    def frontend_optimization_status(run_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        job = session.scalar(select(Job).where(Job.id == run_id, Job.organization_id == tenant.organization_id, Job.kind == "optimization"))
        if job is None or (tenant.project_id and job.project_id != tenant.project_id):
            raise HTTPException(status_code=404, detail="Optimization run not found")
        return _run_payload(job, include_events=True)

    @app.post("/v1/optimization-runs/{run_id}/cancel", response_model=dict[str, Any], tags=["optimizations"])
    def cancel_optimization(run_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        job = session.scalar(select(Job).where(Job.id == run_id, Job.organization_id == tenant.organization_id, Job.kind == "optimization"))
        if job is None or (tenant.project_id and job.project_id != tenant.project_id):
            raise HTTPException(status_code=404, detail="Optimization run not found")
        if job.status not in {"completed", "failed", "cancelled"}:
            if job.status == "queued":
                job.status = "cancelled"
                job.completed_at = datetime.now(timezone.utc)
            else:
                job.cancel_requested_at = datetime.now(timezone.utc)
            session.commit()
            _append_optimization_event(run_id, tenant, job.project_id, "INFO", {"message": "Cancellation requested", "status": "CANCEL_REQUESTED"}, session, event_id=f"{run_id}:cancel-requested")
        return _run_payload(session.get(Job, run_id), include_events=False)

    @app.get("/v1/eval-runs/{run_id}/cases", response_model=dict[str, Any], tags=["evals"])
    def frontend_eval_cases(run_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        run = _run_for_tenant(run_id, "optimization", tenant, session)
        dataset_id = (run.get("config") or {}).get("dataset_id") or (run.get("config") or {}).get("datasetId")
        if not dataset_id:
            return {"cases": [], "page": {"nextCursor": None}}
        dataset = _dataset_for_request(str(dataset_id), tenant=tenant, project=session.get(Project, run["project_id"]), session=session)
        cases: list[dict[str, Any]] = []
        for case in dataset.cases:
            metadata = case.metadata_json if isinstance(case.metadata_json, dict) else {}
            # Metadata-only by default: prompts remain absent unless explicitly
            # enabled for a project-level content debugging session.
            prompt = case.input_data if os.getenv("AGENTPGO_STORE_CONTENT", "").lower() in {"1", "true", "yes"} else None
            cases.append({"id": case.case_id, "category": str(metadata.get("category", "default")), "prompt": prompt, "baselineScore": float(metadata.get("baselineScore", 0)), "optimizedScore": float(metadata.get("optimizedScore", 0)), "baselineLatencyMs": float(metadata.get("baselineLatencyMs", 0)), "optimizedLatencyMs": float(metadata.get("optimizedLatencyMs", 0)), "status": str(metadata.get("status", "PASS")).upper(), "passed": bool(metadata.get("passed", True)), "diffNote": str(metadata.get("diffNote", ""))})
        return {"cases": cases, "page": {"nextCursor": None}}

    @app.get("/v1/optimizations/{run_id}/candidates", response_model=list[dict[str, Any]], tags=["optimizations"])
    def optimization_candidates(run_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)):
        run = _run_for_tenant(run_id, "optimization", tenant, session); job = session.get(Job, run_id)
        return [{"id": row.candidate_id, **row.result} for row in (job.candidate_results if job else [])]

    @app.get("/v1/optimization-runs/{run_id}/candidates", response_model=dict[str, Any], tags=["optimizations"])
    def frontend_optimization_candidates(run_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        run = _run_for_tenant(run_id, "optimization", tenant, session)
        job = session.get(Job, run_id)
        rows = [{"id": row.candidate_id, **row.result} for row in (job.candidate_results if job else [])]
        return {"candidates": rows, "page": {"nextCursor": None}}

    @app.get("/v1/optimization-runs/{run_id}/events", response_model=dict[str, Any], tags=["optimizations"])
    def frontend_optimization_events(
        run_id: str,
        after: int = Query(default=0, ge=0),
        cursor: str | None = Query(default=None, max_length=64),
        limit: int = Query(default=100, ge=1, le=500),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        tenant: Tenant = Depends(get_tenant),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        job = session.scalar(select(Job).where(Job.id == run_id, Job.organization_id == tenant.organization_id, Job.kind == "optimization"))
        if job is None or (tenant.project_id and job.project_id != tenant.project_id):
            raise HTTPException(status_code=404, detail="Optimization run not found")
        try:
            replay_cursors = [after]
            if cursor is not None:
                replay_cursors.append(int(cursor))
            if last_event_id:
                replay_cursors.append(int(last_event_id))
            start_cursor = max(replay_cursors)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Event cursor must be numeric") from exc
        event_cls = getattr(api_models, "OptimizationEvent", None)
        rows = session.scalars(
            select(event_cls)
            .where(event_cls.job_id == run_id, event_cls.sequence > start_cursor)
            .order_by(event_cls.sequence)
            .limit(limit + 1)
        ).all() if event_cls is not None else []
        has_more = len(rows) > limit
        rows = rows[:limit]
        events = [{"id": row.event_id, "sequence": row.sequence, "type": row.event_type, "timestamp": row.created_at.isoformat(), **(row.payload or {})} for row in rows]
        next_cursor = str(rows[-1].sequence) if has_more and rows else None
        return {"events": events, "data": events, "status": str(job.status).upper(), "lastEventId": events[-1]["sequence"] if events else start_cursor, "page": {"nextCursor": next_cursor}}

    @app.get("/v1/optimization-runs/{run_id}/events/stream", tags=["optimizations"])
    def frontend_optimization_event_stream(
        run_id: str,
        request: Request,
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
        tenant: Tenant = Depends(get_tenant),
        session: Session = Depends(get_session),
    ) -> StreamingResponse:
        job = session.scalar(select(Job).where(Job.id == run_id, Job.organization_id == tenant.organization_id, Job.kind == "optimization"))
        if job is None or (tenant.project_id and job.project_id != tenant.project_id):
            raise HTTPException(status_code=404, detail="Optimization run not found")
        try:
            start_cursor = int(last_event_id or 0)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Last-Event-ID must be numeric") from exc
        factory = app.state.session_factory

        async def stream():
            cursor = max(0, start_cursor)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if await request.is_disconnected():
                    return
                with factory() as stream_session:
                    current = stream_session.scalar(select(Job).where(Job.id == run_id, Job.organization_id == tenant.organization_id, Job.kind == "optimization"))
                    event_cls = getattr(api_models, "OptimizationEvent", None)
                    rows = stream_session.scalars(select(event_cls).where(event_cls.job_id == run_id, event_cls.sequence > cursor).order_by(event_cls.sequence).limit(500)).all() if event_cls is not None else []
                    for row in rows:
                        cursor = row.sequence
                        payload = {"id": row.event_id, "sequence": row.sequence, "type": row.event_type, "timestamp": row.created_at.isoformat(), **(row.payload or {})}
                        yield f"id: {row.sequence}\nevent: optimization\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
                    if current is not None and str(current.status) in {"completed", "failed", "cancelled"}:
                        yield f"event: terminal\ndata: {json.dumps({'status': str(current.status).upper()})}\n\n"
                        return
                yield ": heartbeat\n\n"
                await asyncio.sleep(1)

        # The async generator keeps the event loop responsive during the
        # bounded polling window; clients reconnect using event IDs.
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/v1/optimization-runs/{run_id}/select", response_model=dict[str, Any], tags=["optimizations"])
    def select_frontend_candidate(run_id: str, payload: dict[str, Any], tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        run = _run_for_tenant(run_id, "optimization", tenant, session)
        candidate_id = str(payload.get("candidateId", payload.get("candidate_id", ""))).strip()
        if not candidate_id:
            raise HTTPException(status_code=422, detail="candidateId is required")
        job = session.get(Job, run_id)
        if job is None or str(job.status).lower() != "completed":
            raise HTTPException(status_code=409, detail="Candidate selection requires a completed optimization run")
        result_cls = getattr(api_models, "OptimizationResult", None)
        stored = session.scalar(select(result_cls).where(result_cls.job_id == run_id, result_cls.organization_id == tenant.organization_id)) if result_cls is not None else None
        if stored is None or str(stored.status).lower() != "completed":
            raise HTTPException(status_code=409, detail="Verified optimization result is not ready")
        row = next((item for item in (job.candidate_results if job else []) if item.candidate_id == candidate_id), None)
        if row is None:
            raise HTTPException(status_code=404, detail="Candidate not found")
        gate_rows = (stored.metadata_json or {}).get("statistical_gate", [])
        if isinstance(gate_rows, list):
            gate = next((item for item in gate_rows if isinstance(item, dict) and item.get("candidate_id") == candidate_id), None)
            if gate is not None and gate.get("accepted") is not True:
                raise HTTPException(status_code=409, detail="Candidate did not pass the statistical quality gate")
        recommendation = dict(row.result or {})
        recommendation["candidateId"] = candidate_id
        recommendation["selected"] = True
        job.result = {**(job.result or {}), "recommendation": recommendation}
        if result_cls is not None:
            stored.recommendation = recommendation
        session.commit()
        project = session.get(Project, job.project_id) if job and job.project_id else None
        return serialize_project(project, _latest_version(project, session)) if project is not None else recommendation

    @app.get("/v1/optimizations/{run_id}/recommendation", response_model=dict[str, Any], tags=["optimizations"])
    def optimization_recommendation(run_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)):
        run = _run_for_tenant(run_id, "optimization", tenant, session); cls = getattr(api_models, "OptimizationResult", None)
        result = session.scalar(select(cls).where(cls.job_id == run_id, cls.organization_id == tenant.organization_id)) if cls is not None else None
        recommendation = getattr(result, "recommendation", None) or (run.get("result") or {}).get("recommendation")
        if recommendation is None: raise HTTPException(status_code=409, detail="Recommendation is not ready")
        return recommendation

    @app.get("/v1/optimization-runs/{run_id}/recommendation", response_model=dict[str, Any], tags=["optimizations"])
    def frontend_optimization_recommendation(run_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)):
        return optimization_recommendation(run_id, tenant=tenant, session=session)

    def _persisted_export(reference: str | None, run_id: str | None, tenant: Tenant, session: Session) -> dict[str, Any]:
        project = _project_reference(reference, tenant=tenant, session=session); cls = getattr(api_models, "OptimizationResult", None)
        if cls is None: raise HTTPException(status_code=503, detail="Optimization result persistence is unavailable")
        query = select(cls).where(cls.organization_id == tenant.organization_id, cls.project_id == project.id)
        if run_id: query = query.where(cls.job_id == run_id)
        result = session.scalars(query.order_by(cls.created_at.desc())).first()
        if result is None or getattr(result, "recommendation", None) is None: raise HTTPException(status_code=409, detail="Recommendation is not ready")
        payload = {
            "project": project.slug,
            "project_id": project.id,
            "run_id": result.job_id,
            "status": result.status,
            "recommendation": result.recommendation,
            # Export is derived exclusively from the persisted recommendation;
            # this marker lets clients distinguish it from browser-local state.
            "verified": True,
            "format": "json",
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        metadata = getattr(result, "metadata_json", None) or getattr(result, "metadata", None)
        if metadata: payload["metadata"] = metadata
        return payload

    @app.get("/v1/optimization-runs/{run_id}/export", response_model=dict[str, Any], tags=["optimizations"])
    def frontend_export(run_id: str, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)) -> dict[str, Any]:
        run = _run_for_tenant(run_id, "optimization", tenant, session)
        return _persisted_export(run["project_id"], run_id, tenant, session)

    @app.get("/v1/policy/export", response_model=dict[str, Any], include_in_schema=False, tags=["optimizations"])
    def policy_export(project: str | None = None, project_id: str | None = None, run_id: str | None = None, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)): return _persisted_export(project_id or project, run_id, tenant, session)

    @app.post("/v1/config/export", response_model=dict[str, Any], tags=["optimizations"])
    def export_config(payload: dict[str, Any], request: Request, tenant: Tenant = Depends(get_tenant), session: Session = Depends(get_session)):
        project = _project_reference(payload.get("project_id", payload.get("project")), tenant=tenant, session=session)
        result = {"organization_id": tenant.organization_id, "project_id": project.id, "format": "yaml", "config": payload.get("config", payload)}
        if payload.get("run_id"):
            result.update(_persisted_export(project.id, payload["run_id"], tenant, session))
        return result

    # Register the billing routes before aliasing so they are exposed under
    # both /v1 and /api/v1 without duplicating endpoint implementations.
    register_billing_routes(app, get_tenant=get_tenant, get_user=get_user, get_session=get_session)

    # Register browser-facing aliases after the canonical /v1 routes have been
    # declared. Cloning APIRoute metadata preserves response models,
    # dependencies (including JSON/content validation), and auth behavior.
    from fastapi.routing import APIRoute

    for route in list(app.routes):
        if not isinstance(route, APIRoute) or not route.path.startswith("/v1"):
            continue
        alias = "/api" + route.path
        app.add_api_route(
            alias,
            route.endpoint,
            methods=sorted(route.methods or set()),
            response_model=route.response_model,
            status_code=route.status_code,
            response_class=route.response_class,
            response_model_include=route.response_model_include,
            response_model_exclude=route.response_model_exclude,
            response_model_by_alias=route.response_model_by_alias,
            response_model_exclude_unset=route.response_model_exclude_unset,
            response_model_exclude_defaults=route.response_model_exclude_defaults,
            response_model_exclude_none=route.response_model_exclude_none,
            tags=route.tags,
            dependencies=route.dependencies,
            include_in_schema=False,
            name=f"{route.name}_api_v1",
        )

    return app


# Starlette imports this name at middleware execution time; keeping it local
# avoids exposing a second HTTP framework dependency in the module API.
from starlette.responses import JSONResponse, StreamingResponse
app = create_app()
