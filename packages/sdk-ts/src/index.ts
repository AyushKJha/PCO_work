import {
  AGENTPGO_INSTRUMENTATION_NAME,
  AGENTPGO_SCHEMA_VERSION,
  createSpanRecord,
  nowUnixNano,
  sanitizeAttributes,
  toOtlpTracePayload,
  type AttributeValue,
  type SpanKind,
  type SpanRecord,
  type SpanStatusCode,
} from "../../otel-schema/src/index.ts";

export interface ExportResult { sent: number; dropped: number; error?: Error; }
export interface AgentPGOConfig {
  apiKey?: string;
  endpoint?: string;
  serviceName?: string;
  serviceVersion?: string;
  environment?: string;
  projectId?: string;
  enabled?: boolean;
  flushIntervalMs?: number;
  maxQueueSize?: number;
  fetch?: typeof globalThis.fetch;
  headers?: Record<string, string>;
  onExport?: (spans: SpanRecord[]) => void | Promise<void>;
}
export interface TraceRequest<T> { node: string; model: string; run: () => T | Promise<T>; provider?: string; }

export interface StartSpanOptions {
  traceId?: string;
  parentSpanId?: string;
  kind?: SpanKind;
  attributes?: Record<string, unknown>;
}
export interface EndSpanOptions {
  status?: SpanStatusCode;
  statusMessage?: string;
  attributes?: Record<string, unknown>;
  inputTokens?: number;
  outputTokens?: number;
  totalTokens?: number;
  costUsd?: number;
}

const DEFAULT_ENDPOINT = "https://api.agentpgo.dev/v1/traces";
const DEFAULT_SERVICE = "agentpgo-client";

function errorFrom(value: unknown): Error {
  return value instanceof Error ? value : new Error(String(value));
}

export class AgentPGOSpan {
  private readonly record: SpanRecord;
  private ended = false;
  private readonly client: AgentPGOClient;

  constructor(client: AgentPGOClient, record: SpanRecord) {
    this.client = client;
    this.record = record;
  }

  get spanId(): string { return this.record.spanId; }
  get traceId(): string { return this.record.traceId; }
  get isEnded(): boolean { return this.ended; }

  setAttribute(key: string, value: unknown): this {
    if (!this.ended) Object.assign(this.record.attributes, sanitizeAttributes({ [key]: value }));
    return this;
  }

  setAttributes(values: Record<string, unknown>): this {
    if (!this.ended) Object.assign(this.record.attributes, sanitizeAttributes(values));
    return this;
  }

  addEvent(name: string, attributes?: Record<string, unknown>): this {
    if (!this.ended) this.record.events.push({ name, timeUnixNano: nowUnixNano(), attributes: sanitizeAttributes(attributes) });
    return this;
  }

  end(options: EndSpanOptions = {}): void {
    if (this.ended) return;
    this.ended = true;
    Object.assign(this.record.attributes, sanitizeAttributes(options.attributes));
    const usage: Record<string, number> = {};
    if (options.inputTokens !== undefined) usage["gen_ai.usage.input_tokens"] = options.inputTokens;
    if (options.outputTokens !== undefined) usage["gen_ai.usage.output_tokens"] = options.outputTokens;
    if (options.totalTokens !== undefined) usage["gen_ai.usage.total_tokens"] = options.totalTokens;
    if (options.costUsd !== undefined) usage["agentpgo.cost.usd"] = options.costUsd;
    Object.assign(this.record.attributes, usage);
    this.record.endTimeUnixNano = nowUnixNano();
    this.record.status = { code: options.status ?? "unset", ...(options.statusMessage ? { message: options.statusMessage } : {}) };
    this.client.capture(this.record);
  }

  async run<T>(operation: () => T | Promise<T>): Promise<T> {
    try {
      const result = await operation();
      this.end({ status: "ok" });
      return result;
    } catch (error) {
      const normalized = errorFrom(error);
      this.end({ status: "error", statusMessage: normalized.message.slice(0, 512) });
      throw error;
    }
  }
}

export class AgentPGOClient {
  readonly config: Required<Pick<AgentPGOConfig, "serviceName" | "endpoint" | "enabled" | "maxQueueSize">> & AgentPGOConfig;
  private readonly queue: SpanRecord[] = [];
  private timer?: ReturnType<typeof setInterval>;
  private flushing?: Promise<ExportResult>;
  private closed = false;

  constructor(config: AgentPGOConfig = {}) {
    this.config = {
      ...config,
      serviceName: config.serviceName ?? DEFAULT_SERVICE,
      endpoint: config.endpoint ?? DEFAULT_ENDPOINT,
      enabled: config.enabled ?? true,
      maxQueueSize: config.maxQueueSize ?? 1000,
    };
    if (this.config.enabled && this.config.flushIntervalMs && this.config.flushIntervalMs > 0) {
      this.timer = setInterval(() => { void this.flush(); }, this.config.flushIntervalMs);
      if (typeof this.timer === "object" && "unref" in this.timer) this.timer.unref();
    }
  }

  startSpan(name: string, options: StartSpanOptions = {}): AgentPGOSpan {
    const record = createSpanRecord({
      name,
      traceId: options.traceId,
      parentSpanId: options.parentSpanId,
      kind: options.kind,
      attributes: options.attributes,
      instrumentationScope: { name: AGENTPGO_INSTRUMENTATION_NAME, version: AGENTPGO_SCHEMA_VERSION },
    });
    return new AgentPGOSpan(this, record);
  }

  async trace<T>(name: string, operation: (span: AgentPGOSpan) => T | Promise<T>, options?: StartSpanOptions): Promise<T>;
  async trace<T>(request: TraceRequest<T>): Promise<T>;
  async trace<T>(nameOrRequest: string | TraceRequest<T>, operation?: (span: AgentPGOSpan) => T | Promise<T>, options: StartSpanOptions = {}): Promise<T> {
    if (typeof nameOrRequest !== "string") {
      const request = nameOrRequest;
      const span = this.startSpan(`agent.node.${request.node}`, { attributes: { "agentpgo.node": request.node, "gen_ai.request.model": request.model, ...(request.provider ? { "gen_ai.system": request.provider } : {}) } });
      return span.run(request.run);
    }
    const span = this.startSpan(nameOrRequest, options);
    return span.run(() => operation!(span));
  }

  capture(span: SpanRecord): void {
    if (this.closed || !this.config.enabled) return;
    if (this.config.onExport) void this.config.onExport([span]);
    if (!this.config.endpoint || this.config.onExport) return;
    if (this.queue.length >= this.config.maxQueueSize) this.queue.shift();
    this.queue.push(span);
  }

  async flush(): Promise<ExportResult> {
    if (this.flushing) return this.flushing;
    if (!this.config.enabled || !this.queue.length || !this.config.endpoint) return { sent: 0, dropped: 0 };
    const batch = this.queue.splice(0, this.queue.length);
    this.flushing = this.export(batch).finally(() => { this.flushing = undefined; });
    return this.flushing;
  }

  private async export(batch: SpanRecord[]): Promise<ExportResult> {
    const fetcher = this.config.fetch ?? globalThis.fetch;
    if (!fetcher) return { sent: 0, dropped: batch.length, error: new Error("No fetch implementation available") };
    try {
      const headers = { "content-type": "application/json", ...this.config.headers };
      if (this.config.apiKey) headers.authorization = `Bearer ${this.config.apiKey}`;
      if (this.config.projectId) headers["X-AgentPGO-Project-ID"] = this.config.projectId;
      const response = await fetcher(this.config.endpoint, { method: "POST", headers, body: JSON.stringify(toOtlpTracePayload(batch, this.config)) });
      if (!response.ok) throw new Error(`OTLP export failed with HTTP ${response.status}`);
      return { sent: batch.length, dropped: 0 };
    } catch (error) {
      return { sent: 0, dropped: batch.length, error: errorFrom(error) };
    }
  }

  async shutdown(): Promise<ExportResult> {
    if (this.closed) return { sent: 0, dropped: 0 };
    this.closed = true;
    if (this.timer) clearInterval(this.timer);
    return this.flush();
  }
}

let defaultClient: AgentPGOClient | undefined;
export function init(config: AgentPGOConfig = {}): AgentPGOClient {
  defaultClient?.shutdown();
  defaultClient = new AgentPGOClient(config);
  return defaultClient;
}
export function getClient(): AgentPGOClient {
  return defaultClient ?? (defaultClient = new AgentPGOClient());
}
export async function trace<T>(name: string, operation: (span: AgentPGOSpan) => T | Promise<T>, options?: StartSpanOptions): Promise<T>;
export async function trace<T>(request: TraceRequest<T>): Promise<T>;
export async function trace<T>(nameOrRequest: string | TraceRequest<T>, operation?: (span: AgentPGOSpan) => T | Promise<T>, options?: StartSpanOptions): Promise<T> {
  return typeof nameOrRequest === "string" ? getClient().trace(nameOrRequest, operation!, options) : getClient().trace(nameOrRequest);
}

export type { AttributeValue, SpanRecord } from "../../otel-schema/src/index.ts";
