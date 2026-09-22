import { AgentProject, ApiCollection, BaselineRun, EvalCase, EvalRun, EvalRunCase, EvalSuite, EvalSuiteCreateInput, JsonObject, OptimizationCandidate, OptimizationRecommendation, OptimizationRun, OptimizerEvent, ProfileRun, ProjectLayout, ProjectSettings, ProjectSetupState, TraceDetail, TraceSpan, EntitlementState, ReferralSummary } from "../types";
import { adaptBaselineRun, adaptEvalCase, adaptEvalRun, adaptEvalRunCase, adaptEvalSuite, adaptLayout, adaptOptimizationRun, adaptRecommendation, adaptProject, adaptCandidate, adaptOptimizerEvent, adaptOnboarding, adaptProfileRun, adaptSettings, adaptTraceDetail, adaptTraceSpan } from "./adapters";

const configuredBase = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.trim();
const defaultApiBase = ["/api", "v1"].join("/");
export const API_BASE_URL = (configuredBase || defaultApiBase).replace(/\/$/, "");
export const DEMO_ACCESS_TOKEN = (import.meta.env.VITE_DEMO_ACCESS_TOKEN as string | undefined)?.trim() || "";
// Explicitly opt-in test-only bootstrap. Real sessions must not silently
// become a demo identity when a browser reloads or opens a new tab.
export const DEMO_AUTH_ENABLED = (import.meta.env.VITE_DEMO_AUTH_ENABLED as string | undefined)?.trim().toLowerCase() === "true";

export class ApiError extends Error {
  status: number;
  code: string;
  requestId?: string;
  fields: Record<string, string>;

  constructor(status: number, message: string, code = "REQUEST_FAILED", requestId?: string, fields: Record<string, string> = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.fields = fields;
  }
}

type RequestOptions = RequestInit & { skipAuth?: boolean; unwrap?: boolean };

const unwrap = <T>(payload: unknown): T => {
  if (payload && typeof payload === "object" && "data" in payload) return (payload as { data: T }).data;
  return payload as T;
};

const token = () => typeof window === "undefined" ? "" : window.sessionStorage.getItem("twinerun.access-token") || "";

let demoTokenRequest: Promise<string | null> | null = null;

const storeToken = (accessToken: string) => {
  if (typeof window !== "undefined") window.sessionStorage.setItem("twinerun.access-token", accessToken);
};

const clearRuntimeToken = () => {
  if (typeof window !== "undefined") window.sessionStorage.removeItem("twinerun.access-token");
};

/** Restore a short-lived test token when a localStorage demo session survives
 * a refresh or opens in a new tab. This path is never enabled by default. */
const ensureDemoAccessToken = async (): Promise<string | null> => {
  const existing = token();
  if (existing || !DEMO_AUTH_ENABLED) return existing || null;
  if (!demoTokenRequest) {
    demoTokenRequest = request<{ accessToken?: string }>("/auth/demo", { method: "POST", body: "{}", skipAuth: true })
      .then((auth) => {
        const accessToken = typeof auth.accessToken === "string" ? auth.accessToken.trim() : "";
        if (accessToken) storeToken(accessToken);
        return accessToken || null;
      })
      .catch(() => null)
      .finally(() => { demoTokenRequest = null; });
  }
  return demoTokenRequest;
};

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { skipAuth = false, unwrap: shouldUnwrap = true, ...requestInit } = options;
  let accessToken = token();
  const canBootstrap = DEMO_AUTH_ENABLED && !skipAuth && !path.startsWith("/auth/");
  if (!accessToken && canBootstrap) accessToken = await ensureDemoAccessToken();
  let response: Response;
  for (let attempt = 0; ; attempt += 1) {
    const headers = new Headers(requestInit.headers);
    headers.set("Accept", "application/json");
    if (requestInit.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    if (accessToken && !skipAuth) headers.set("Authorization", `Bearer ${accessToken}`);
    const requestId = typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
    headers.set("X-Request-ID", requestId);
    response = await fetch(`${API_BASE_URL}${path}`, { ...requestInit, headers });
    if (response.status !== 401 || !canBootstrap || attempt > 0 || !accessToken || DEMO_ACCESS_TOKEN) break;
    clearRuntimeToken();
    accessToken = await ensureDemoAccessToken();
    if (!accessToken) break;
  }
  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try { payload = JSON.parse(text); } catch { payload = text; }
  }
  if (!response.ok) {
    if (response.status >= 500) {
      throw new ApiError(response.status, "The backend is temporarily unavailable. Please try again shortly.", "SERVICE_UNAVAILABLE", response.headers.get("x-request-id") || undefined);
    }
    const error = payload && typeof payload === "object" && "error" in payload ? (payload as { error: Record<string, unknown> }).error : payload;
    const details = (error && typeof error === "object" ? error : {}) as Record<string, unknown>;
    throw new ApiError(response.status, String(details.message || (typeof payload === "string" ? payload : "Request failed.")), String(details.code || "REQUEST_FAILED"), String(details.requestId || response.headers.get("x-request-id") || ""), (details.fields as Record<string, string>) || {});
  }
  return shouldUnwrap ? unwrap<T>(payload) : payload as T;
}

const json = (value: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(value) });

const query = (params: Record<string, string | number | undefined>) => {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => { if (value !== undefined) search.set(key, String(value)); });
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
};

const collection = <T>(payload: ApiCollection<unknown> | unknown[], adapt: (item: unknown) => T): ApiCollection<T> => {
  if (Array.isArray(payload)) return { data: payload.map(adapt), page: { nextCursor: null } };
  return { data: (payload.data || []).map(adapt), page: { nextCursor: payload.page?.nextCursor ?? null } };
};

export const api = {
  async demoSignIn(): Promise<{ accessToken: string; tokenType: string; expiresIn: number }> {
    const auth = await request<{ accessToken: string; tokenType: string; expiresIn: number }>("/auth/demo", { ...json({}), skipAuth: true });
    if (auth.accessToken) storeToken(auth.accessToken);
    return auth;
  },
  async me(): Promise<Record<string, unknown>> { return request<Record<string, unknown>>("/me"); },
  async systemOverview(): Promise<Record<string, unknown>> { return request<Record<string, unknown>>("/system/overview"); },
  async signIn(email: string, password: string): Promise<Record<string, unknown>> {
    const auth = await request<Record<string, unknown>>("/auth/signin", json({ email, password }));
    if (typeof auth.accessToken === "string") storeToken(auth.accessToken);
    return auth;
  },
  async signUp(name: string, email: string, password: string, referralCode?: string): Promise<Record<string, unknown>> {
    const auth = await request<Record<string, unknown>>("/auth/signup", json({ name, email, password, ...(referralCode ? { referralCode } : {}) }));
    if (typeof auth.accessToken === "string") storeToken(auth.accessToken);
    return auth;
  },
  async logout(): Promise<void> { await request("/auth/logout", { method: "POST" }); },
  async checkout(plan: "pro", referralCode?: string, idempotencyKey?: string): Promise<{ checkoutUrl: string; checkoutSessionId: string }> {
    const payload = await request<Record<string, unknown>>("/billing/checkout", json({ plan, ...(referralCode ? { referralCode } : {}), idempotencyKey: idempotencyKey || crypto.randomUUID() }));
    return { checkoutUrl: String(payload.checkoutUrl || payload.checkout_url || ""), checkoutSessionId: String(payload.checkoutSessionId || payload.checkout_session_id || payload.id || "") };
  },
  async entitlements(): Promise<EntitlementState> { return request<EntitlementState>("/entitlements"); },
  async referrals(): Promise<ReferralSummary> { return request<ReferralSummary>("/referrals"); },
  async generateReferralCode(): Promise<ReferralSummary> { return request<ReferralSummary>("/referrals/code", { method: "POST", body: JSON.stringify({}) }); },
  async billingPortal(): Promise<{ url?: string; portalUrl?: string }> { return request<{ url?: string; portalUrl?: string }>("/billing/portal", { method: "POST", body: JSON.stringify({}) }); },
  async projects(): Promise<AgentProject[]> {
    const payload = await request<unknown>("/projects");
    const list = Array.isArray(payload) ? payload : ((payload as { projects?: unknown[] } | null)?.projects || []);
    return list.map((item) => adaptProject(item));
  },
  async createProject(name: string, slug: string): Promise<AgentProject> { return adaptProject(await request("/projects", json({ name, slug }))); },
  async onboarding(projectId: string): Promise<ProjectSetupState> { return adaptOnboarding(await request(`/projects/${encodeURIComponent(projectId)}/onboarding`)); },
  async createVersion(projectId: string, input: Record<string, unknown>): Promise<Record<string, unknown>> { return request(`/projects/${encodeURIComponent(projectId)}/versions`, json(input)); },
  async createProjectKey(projectId: string, name = "twinerun-local"): Promise<{ secret: string; name: string; id?: string }> { return request(`/projects/${encodeURIComponent(projectId)}/api-keys`, json({ name })); },
  async startProfile(projectId: string, input: JsonObject = {}): Promise<ProfileRun> { return adaptProfileRun(await request(`/profiles`, json({ ...input, project_id: projectId }))); },
  async profile(profileId: string): Promise<ProfileRun> { return adaptProfileRun(await request(`/profiles/${encodeURIComponent(profileId)}`)); },
  async traces(projectId: string, params: { cursor?: string; limit?: number } = {}): Promise<ApiCollection<TraceSpan>> {
    const payload = await request<ApiCollection<unknown>>(`/projects/${encodeURIComponent(projectId)}/traces${query(params)}`, { unwrap: false });
    return collection(payload, adaptTraceSpan);
  },
  async trace(projectId: string, traceId: string): Promise<TraceDetail> { return adaptTraceDetail(await request(`/projects/${encodeURIComponent(projectId)}/traces/${encodeURIComponent(traceId)}`)); },
  async evalSuites(projectId: string): Promise<ApiCollection<EvalSuite>> {
    const payload = await request<ApiCollection<unknown>>(`/projects/${encodeURIComponent(projectId)}/eval-suites`, { unwrap: false });
    return collection(payload, adaptEvalSuite);
  },
  async evalSuite(suiteId: string): Promise<EvalSuite> { return adaptEvalSuite(await request(`/eval-suites/${encodeURIComponent(suiteId)}`)); },
  async createEvalSuite(projectId: string, input: EvalSuiteCreateInput): Promise<EvalSuite> { return adaptEvalSuite(await request(`/projects/${encodeURIComponent(projectId)}/eval-suites`, json(input))); },
  async evalRuns(projectId: string): Promise<ApiCollection<EvalRun>> {
    const payload = await request<ApiCollection<unknown>>(`/projects/${encodeURIComponent(projectId)}/eval-runs`, { unwrap: false });
    return collection(payload, adaptEvalRun);
  },
  async startEvalRun(suiteId: string, input: { projectVersionId?: string; candidateConfig?: JsonObject; sampleCount?: number } = {}): Promise<EvalRun> { return adaptEvalRun(await request(`/eval-suites/${encodeURIComponent(suiteId)}/runs`, json(input))); },
  async evalRun(runId: string): Promise<EvalRun> { return adaptEvalRun(await request(`/eval-runs/${encodeURIComponent(runId)}`)); },
  async evalRunCases(runId: string, limit = 100): Promise<ApiCollection<EvalRunCase>> {
    const payload = await request<ApiCollection<unknown>>(`/eval-runs/${encodeURIComponent(runId)}/cases?limit=${encodeURIComponent(String(limit))}`, { unwrap: false });
    return collection(payload, adaptEvalRunCase);
  },
  async importEval(projectId: string, name: string, cases: Record<string, unknown>[], graders: Record<string, unknown>[]): Promise<Record<string, unknown>> { return request("/evals/import", json({ project_id: projectId, name, cases, graders })); },
  async runBaseline(projectId: string, datasetId?: string): Promise<BaselineRun> {
    const payload = await request<Record<string, unknown>>("/baselines/run", json({ project_id: projectId, dataset_id: datasetId, config: {}, max_experiment_cost_usd: 25 }));
    return adaptBaselineRun(payload);
  },
  async baseline(runId: string): Promise<BaselineRun> { return adaptBaselineRun(await request(`/baselines/${encodeURIComponent(runId)}`)); },
  async project(projectId: string): Promise<AgentProject> { return adaptProject(await request(`/projects/${encodeURIComponent(projectId)}`)); },
  async startOptimization(projectId: string, input: Record<string, unknown>): Promise<{ runId: string; status: string }> {
    const payload = await request<Record<string, unknown>>(`/projects/${encodeURIComponent(projectId)}/optimization-runs`, json(input));
    return { runId: String(payload.runId || payload.run_id || payload.id), status: String(payload.status || "QUEUED") };
  },
  async optimizationRuns(projectId: string): Promise<ApiCollection<OptimizationRun>> {
    const payload = await request<ApiCollection<unknown>>(`/projects/${encodeURIComponent(projectId)}/optimization-runs`, { unwrap: false });
    return collection(payload, adaptOptimizationRun);
  },
  async optimizationRun(runId: string): Promise<OptimizationRun> { return adaptOptimizationRun(await request(`/optimization-runs/${encodeURIComponent(runId)}`)); },
  async optimizationEvents(runId: string): Promise<OptimizerEvent[]> {
    const payload = await request<unknown>(`/optimization-runs/${encodeURIComponent(runId)}/events`);
    const list = Array.isArray(payload) ? payload : ((payload as { events?: unknown[] } | null)?.events || []);
    return list.map((item) => adaptOptimizerEvent(item));
  },
  async candidates(runId: string): Promise<OptimizationCandidate[]> {
    const payload = await request<unknown>(`/optimization-runs/${encodeURIComponent(runId)}/candidates`);
    const list = Array.isArray(payload) ? payload : ((payload as { candidates?: unknown[] } | null)?.candidates || []);
    return list.map((item) => adaptCandidate(item));
  },
  async recommendation(runId: string): Promise<OptimizationRecommendation> { return adaptRecommendation(await request(`/optimization-runs/${encodeURIComponent(runId)}/recommendation`)); },
  async selectCandidate(runId: string, candidateId: string): Promise<AgentProject | null> {
    const payload = await request<unknown>(`/optimization-runs/${encodeURIComponent(runId)}/select`, json({ candidateId }));
    return payload ? adaptProject(payload) : null;
  },
  async evalCases(runId: string): Promise<EvalCase[]> {
    const payload = await request<unknown>(`/eval-runs/${encodeURIComponent(runId)}/cases`);
    const list = Array.isArray(payload) ? payload : ((payload as { cases?: unknown[] } | null)?.cases || []);
    return list.map((item) => adaptEvalCase(item));
  },
  async settings(projectId: string): Promise<ProjectSettings> { return adaptSettings(await request(`/projects/${encodeURIComponent(projectId)}/settings`)); },
  async updateSettings(projectId: string, settings: Partial<ProjectSettings> | JsonObject): Promise<ProjectSettings> { return adaptSettings(await request(`/projects/${encodeURIComponent(projectId)}/settings`, jsonPath("", settings, "PATCH"))); },
  async layout(projectId: string): Promise<ProjectLayout> { return adaptLayout(await request(`/projects/${encodeURIComponent(projectId)}/layout`)); },
  async updateLayout(projectId: string, layout: Pick<ProjectLayout, "nodes"> & Partial<Pick<ProjectLayout, "versionId" | "revision">>): Promise<ProjectLayout> { return adaptLayout(await request(`/projects/${encodeURIComponent(projectId)}/layout`, json(layout))); },
  async exportRun(runId: string): Promise<Blob> {
    const headers = new Headers({ Accept: "application/yaml, application/json" });
    const accessToken = token();
    if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
    const response = await fetch(`${API_BASE_URL}/optimization-runs/${encodeURIComponent(runId)}/export`, { headers });
    if (!response.ok) throw new ApiError(response.status, "Export is not available for this run.");
    return response.blob();
  },
};

function jsonPath(path: string, value: unknown, method = "POST"): RequestOptions {
  return { ...json(value), method, ...(path ? {} : {}) };
}
