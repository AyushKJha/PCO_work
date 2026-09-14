"""Versioned contracts shared by the API, worker, and optimizer.

The HTTP layer historically accepted untyped dictionaries for run payloads.
These models intentionally keep unknown fields so older clients can continue
to send provider-specific options while the fields owned by AgentPGO are
validated and normalized in one place.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal
import math

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CONTRACT_VERSION = "v1"


class VersionedContract(BaseModel):
    """Base for wire contracts with a stable schema marker."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)
    schema_version: Literal[CONTRACT_VERSION] = CONTRACT_VERSION


class CandidateConfig(VersionedContract):
    """A candidate agent/provider configuration evaluated by a job.

    ``parameters`` contains provider-specific settings.  ``config`` is kept
    as a compatibility alias for callers that used the optimizer's original
    candidate shape.  Cost/latency/quality are optional measurements when a
    candidate is submitted and are persisted in result metadata when known.
    """

    id: str = Field(default="baseline", min_length=1, max_length=255)
    provider: str | None = Field(default=None, min_length=1, max_length=128)
    model: str | None = Field(default=None, min_length=1, max_length=255)
    parameters: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    cost_usd: float = Field(default=0.0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0)
    quality: float | None = None

    @model_validator(mode="before")
    @classmethod
    def normalize_parameter_aliases(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        if "parameters" not in data and isinstance(data.get("params"), Mapping):
            data["parameters"] = dict(data["params"])
        if "parameters" not in data and isinstance(data.get("config"), Mapping):
            data["parameters"] = dict(data["config"])
        return data

    @field_validator("cost_usd", "latency_ms", "quality")
    @classmethod
    def finite_measurement(cls, value: float | None) -> float | None:
        if value is None:
            return None
        # Pydantic's ge constraint rejects negatives; rejecting NaN/Infinity
        # keeps JSON serialization deterministic across Python and JS clients.
        if not math.isfinite(float(value)):
            raise ValueError("measurement must be finite")
        return float(value)


class OptimizationConfig(VersionedContract):
    """Search and evaluation settings for an optimization run."""

    dataset_id: str | None = Field(default=None, min_length=1, max_length=255)
    baseline: CandidateConfig | None = None
    candidates: list[CandidateConfig] = Field(default_factory=list)
    beam_width: int = Field(default=3, ge=1)
    halving_rounds: int = Field(default=2, ge=1)
    initial_budget: int = Field(default=1, ge=1)
    max_quality_regression: float = Field(default=0.0, ge=0)
    max_experiment_cost_usd: float = Field(default=25.0, gt=0)

    @model_validator(mode="before")
    @classmethod
    def normalize_candidate_aliases(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        if "candidates" not in data:
            for key in ("candidate_configs", "candidate_pool"):
                if key in data:
                    data["candidates"] = data[key]
                    break
        return data

    @field_validator("max_quality_regression", "max_experiment_cost_usd")
    @classmethod
    def finite_limits(cls, value: float) -> float:
        if value != value or value in {float("inf"), float("-inf")}:
            raise ValueError("limit must be finite")
        return float(value)


class JobPayload(VersionedContract):
    """Durable worker payload.

    The current queue only publishes ``job_id`` and ``kind``; the full
    payload is stored in PostgreSQL.  ``config.candidates`` is the legacy API
    shape, while ``candidates`` at the top level is the worker-friendly shape.
    Validation makes both representations equivalent without changing either
    caller.
    """

    job_id: str | None = Field(default=None, min_length=1, max_length=255)
    kind: str = Field(default="optimization", min_length=1, max_length=64)
    organization_id: str | None = Field(default=None, min_length=1, max_length=255)
    project_id: str | None = Field(default=None, min_length=1, max_length=255)
    dataset_id: str | None = Field(default=None, min_length=1, max_length=255)
    config: OptimizationConfig = Field(default_factory=OptimizationConfig)
    candidates: list[CandidateConfig] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_shape(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        raw_config = data.get("config")
        config = dict(raw_config) if isinstance(raw_config, Mapping) else {}
        if "dataset_id" not in data and config.get("dataset_id") is not None:
            data["dataset_id"] = config["dataset_id"]
        if "candidates" not in data and isinstance(config.get("candidates"), list):
            data["candidates"] = config["candidates"]
        elif isinstance(data.get("candidates"), list) and "candidates" not in config:
            config["candidates"] = data["candidates"]
        data["config"] = config
        return data

    @model_validator(mode="after")
    def reject_conflicting_candidates(self) -> "JobPayload":
        if self.candidates and self.config.candidates and [c.model_dump() for c in self.candidates] != [c.model_dump() for c in self.config.candidates]:
            raise ValueError("candidates and config.candidates must match")
        return self

    @model_validator(mode="after")
    def normalize_after_validation(self) -> "JobPayload":
        if not self.candidates and self.config.candidates:
            self.candidates = list(self.config.candidates)
        if self.dataset_id is None:
            self.dataset_id = self.config.dataset_id
        if not self.config.candidates and self.candidates:
            self.config.candidates = list(self.candidates)
        return self

    @property
    def optimization(self) -> OptimizationConfig:
        """Readable alias for callers that distinguish job and optimization."""

        return self.config

    def to_wire(self) -> dict[str, Any]:
        """Return JSON-compatible data suitable for the durable Job payload."""

        return self.model_dump(mode="json", exclude_none=True)


__all__ = ["CONTRACT_VERSION", "CandidateConfig", "OptimizationConfig", "JobPayload"]
