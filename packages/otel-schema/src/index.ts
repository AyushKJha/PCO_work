/**
 * AgentPGO's small, dependency-free OpenTelemetry JSON contract.
 *
 * The SDK and adapters write this shape; the API can ingest it without knowing
 * which JavaScript framework produced a span. Prompt/completion bodies are
 * intentionally not part of the contract and sensitive attribute keys are
 * removed before serialization.
 */

export const AGENTPGO_SCHEMA_VERSION = "1.0.0";
export const AGENTPGO_INSTRUMENTATION_NAME = "@agentpgo/sdk";

export type PrimitiveAttribute = string | number | boolean;
export type AttributeValue = PrimitiveAttribute | PrimitiveAttribute[];
export type SpanKind = "internal" | "server" | "client" | "producer" | "consumer";
export type SpanStatusCode = "unset" | "ok" | "error";

export interface SpanStatus {
  code: SpanStatusCode;
  message?: string;
}

export interface SpanEvent {
  name: string;
  timeUnixNano: string;
  attributes?: Record<string, AttributeValue>;
}

export interface SpanRecord {
  traceId: string;
  spanId: string;
  parentSpanId?: string;
  name: string;
  kind: SpanKind;
  startTimeUnixNano: string;
  endTimeUnixNano?: string;
  attributes: Record<string, AttributeValue>;
  events: SpanEvent[];
  status: SpanStatus;
  instrumentationScope?: { name: string; version?: string };
}

export interface CreateSpanOptions {
  name: string;
  traceId?: string;
  spanId?: string;
  parentSpanId?: string;
  kind?: SpanKind;
  startTimeUnixNano?: string | number | bigint;
  attributes?: Record<string, unknown>;
  instrumentationScope?: { name: string; version?: string };
}

export interface ResourceOptions {
  serviceName: string;
  serviceVersion?: string;
  environment?: string;
  attributes?: Record<string, unknown>;
}

export interface OtlpAnyValue {
  stringValue?: string;
  boolValue?: boolean;
  intValue?: string;
  doubleValue?: number;
  arrayValue?: { values: OtlpAnyValue[] };
}

export interface OtlpKeyValue {
  key: string;
  value: OtlpAnyValue;
}

export interface OtlpSpan {
  traceId: string;
  spanId: string;
  parentSpanId?: string;
  name: string;
  kind: number;
  startTimeUnixNano: string;
  endTimeUnixNano?: string;
  attributes?: OtlpKeyValue[];
  events?: Array<{ name: string; timeUnixNano: string; attributes?: OtlpKeyValue[] }>;
  status?: { code: number; message?: string };
}

export interface OtlpTracePayload {
  resourceSpans: Array<{
    resource: { attributes: OtlpKeyValue[] };
    scopeSpans: Array<{
      scope: { name: string; version?: string };
      spans: OtlpSpan[];
    }>;
  }>;
}

const SENSITIVE_KEY = /(prompt|completion|password|secret|api[-_.]?key|authorization|cookie|raw[_\-. ]?(?:input|output|content|text)|document[_\-. ]?text)/i;

function nowUnixNano(): string {
  return String(BigInt(Date.now()) * 1_000_000n);
}

function randomHex(length: number): string {
  const bytes = new Uint8Array(Math.ceil(length / 2));
  const cryptoObject = globalThis.crypto;
  if (cryptoObject?.getRandomValues) cryptoObject.getRandomValues(bytes);
  else for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("").slice(0, length);
}

function validHex(value: string | undefined, length: number): string | undefined {
  return value && new RegExp(`^[0-9a-f]{${length}}$`, "i").test(value) ? value.toLowerCase() : undefined;
}

function attributeValue(value: unknown): AttributeValue | undefined {
  if (typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (Array.isArray(value)) {
    const values = value.filter((item): item is PrimitiveAttribute =>
      typeof item === "string" || typeof item === "boolean" || (typeof item === "number" && Number.isFinite(item)),
    );
    return values.length === value.length ? values : undefined;
  }
  return undefined;
}

/** Remove values that could contain customer prompts, outputs, or credentials. */
export function sanitizeAttributes(input: Record<string, unknown> = {}): Record<string, AttributeValue> {
  const output: Record<string, AttributeValue> = {};
  for (const [key, value] of Object.entries(input)) {
    if (SENSITIVE_KEY.test(key)) continue;
    const normalized = attributeValue(value);
    if (normalized !== undefined) output[key] = normalized;
  }
  return output;
}

export function createSpanRecord(options: CreateSpanOptions): SpanRecord {
  const start = options.startTimeUnixNano === undefined ? nowUnixNano() : String(options.startTimeUnixNano);
  return {
    traceId: validHex(options.traceId, 32) ?? randomHex(32),
    spanId: validHex(options.spanId, 16) ?? randomHex(16),
    parentSpanId: validHex(options.parentSpanId, 16),
    name: options.name.slice(0, 256),
    kind: options.kind ?? "internal",
    startTimeUnixNano: start,
    attributes: sanitizeAttributes(options.attributes),
    events: [],
    status: { code: "unset" },
    instrumentationScope: options.instrumentationScope,
  };
}

function otlpValue(value: AttributeValue): OtlpAnyValue {
  if (typeof value === "string") return { stringValue: value };
  if (typeof value === "boolean") return { boolValue: value };
  if (typeof value === "number") {
    return Number.isInteger(value) ? { intValue: String(value) } : { doubleValue: value };
  }
  return { arrayValue: { values: value.map(otlpValue) } };
}

export function attributesToOtlp(input: Record<string, unknown>): OtlpKeyValue[] {
  const attributes = sanitizeAttributes(input);
  return Object.keys(attributes).map((key) => ({ key, value: otlpValue(attributes[key]) }));
}

const KIND: Record<SpanKind, number> = { internal: 1, server: 2, client: 3, producer: 4, consumer: 5 };
const STATUS: Record<SpanStatusCode, number> = { unset: 0, ok: 1, error: 2 };

function toOtlpSpan(span: SpanRecord): OtlpSpan {
  return {
    traceId: span.traceId,
    spanId: span.spanId,
    ...(span.parentSpanId ? { parentSpanId: span.parentSpanId } : {}),
    name: span.name,
    kind: KIND[span.kind],
    startTimeUnixNano: span.startTimeUnixNano,
    ...(span.endTimeUnixNano ? { endTimeUnixNano: span.endTimeUnixNano } : {}),
    attributes: attributesToOtlp(span.attributes),
    events: span.events.map((event) => ({
      name: event.name,
      timeUnixNano: event.timeUnixNano,
      ...(event.attributes ? { attributes: attributesToOtlp(event.attributes) } : {}),
    })),
    status: { code: STATUS[span.status.code], ...(span.status.message ? { message: span.status.message } : {}) },
  };
}

export function toOtlpTracePayload(spans: SpanRecord[], resource: ResourceOptions): OtlpTracePayload {
  const resourceAttributes: Record<string, unknown> = {
    "service.name": resource.serviceName,
    ...(resource.serviceVersion ? { "service.version": resource.serviceVersion } : {}),
    ...(resource.environment ? { "deployment.environment": resource.environment } : {}),
    ...(resource.attributes ?? {}),
    "agentpgo.schema.version": AGENTPGO_SCHEMA_VERSION,
  };
  const grouped = new Map<string, { scope: { name: string; version?: string }; spans: OtlpSpan[] }>();
  for (const span of spans) {
    const scope = span.instrumentationScope ?? { name: AGENTPGO_INSTRUMENTATION_NAME, version: AGENTPGO_SCHEMA_VERSION };
    const key = `${scope.name}\0${scope.version ?? ""}`;
    const bucket = grouped.get(key) ?? { scope, spans: [] };
    bucket.spans.push(toOtlpSpan(span));
    grouped.set(key, bucket);
  }
  return {
    resourceSpans: [{
      resource: { attributes: attributesToOtlp(resourceAttributes) },
      scopeSpans: [...grouped.values()],
    }],
  };
}

export { nowUnixNano };
