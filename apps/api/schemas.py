"""Pydantic representations of the canonical OTLP/HTTP JSON contract."""

from __future__ import annotations

from typing import Any, Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


HEX32 = r"^[0-9a-fA-F]{32}$"
HEX16 = r"^[0-9a-fA-F]{16}$"

MAX_RESOURCE_SPANS = 64
MAX_SCOPE_SPANS = 64
MAX_SPANS_PER_SCOPE = 512
MAX_ATTRIBUTES_PER_RECORD = 128
MAX_EVENTS_PER_SPAN = 128
MAX_LINKS_PER_SPAN = 128
MAX_OTLP_SPANS = 2000


class OTLPModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="allow")


class AnyValue(OTLPModel):
    string_value: str | None = Field(default=None, alias="stringValue")
    bool_value: bool | None = Field(default=None, alias="boolValue")
    int_value: int | str | None = Field(default=None, alias="intValue")
    double_value: float | None = Field(default=None, alias="doubleValue")
    bytes_value: str | None = Field(default=None, alias="bytesValue")
    array_value: dict[str, Any] | None = Field(default=None, alias="arrayValue")
    kvlist_value: dict[str, Any] | None = Field(default=None, alias="kvlistValue")


class KeyValue(OTLPModel):
    key: str = Field(min_length=1, max_length=1024)
    value: AnyValue


class Resource(OTLPModel):
    attributes: list[KeyValue] = Field(default_factory=list, max_length=MAX_ATTRIBUTES_PER_RECORD)


class InstrumentationScope(OTLPModel):
    name: str = Field(default="", max_length=255)
    version: str | None = Field(default=None, max_length=255)
    attributes: list[KeyValue] = Field(default_factory=list, max_length=MAX_ATTRIBUTES_PER_RECORD)


class SpanStatus(OTLPModel):
    code: int | None = Field(default=None, ge=0, le=2)
    message: str | None = None


class Span(OTLPModel):
    trace_id: Annotated[str, Field(alias="traceId", pattern=HEX32)]
    span_id: Annotated[str, Field(alias="spanId", pattern=HEX16)]
    parent_span_id: Annotated[str | None, Field(default=None, alias="parentSpanId", pattern=HEX16)]
    name: str = Field(min_length=1, max_length=1024)
    kind: int = Field(default=0, ge=0, le=5)
    start_time_unix_nano: int | str | None = Field(default=None, alias="startTimeUnixNano")
    end_time_unix_nano: int | str | None = Field(default=None, alias="endTimeUnixNano")
    attributes: list[KeyValue] = Field(default_factory=list, max_length=MAX_ATTRIBUTES_PER_RECORD)
    events: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_EVENTS_PER_SPAN)
    links: list[dict[str, Any]] = Field(default_factory=list, max_length=MAX_LINKS_PER_SPAN)
    status: SpanStatus | None = None


class ScopeSpans(OTLPModel):
    scope: InstrumentationScope | None = None
    spans: list[Span] = Field(default_factory=list, max_length=MAX_SPANS_PER_SCOPE)


class ResourceSpans(OTLPModel):
    resource: Resource | None = None
    scope_spans: list[ScopeSpans] = Field(default_factory=list, max_length=MAX_SCOPE_SPANS, alias="scopeSpans")


class OTLPExportRequest(OTLPModel):
    resource_spans: list[ResourceSpans] = Field(default_factory=list, max_length=MAX_RESOURCE_SPANS, alias="resourceSpans")


    @model_validator(mode="after")
    def bounded_span_count(self) -> "OTLPExportRequest":
        count = sum(len(scope.spans) for resource in self.resource_spans for scope in resource.scope_spans)
        if count > MAX_OTLP_SPANS:
            raise ValueError(f"OTLP batch exceeds {MAX_OTLP_SPANS} spans")
        return self

class IngestionResponse(BaseModel):
    accepted: int = Field(ge=0)
    rejected: int = Field(default=0, ge=0)
