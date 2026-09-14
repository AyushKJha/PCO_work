import assert from "node:assert/strict";
import test from "node:test";
import {
  AGENTPGO_SCHEMA_VERSION,
  attributesToOtlp,
  createSpanRecord,
  toOtlpTracePayload,
} from "../src/index.ts";

test("serializes an AgentPGO span to OTLP JSON with resource and scope", () => {
  const span = createSpanRecord({
    name: "llm.generate",
    traceId: "a".repeat(32),
    spanId: "b".repeat(16),
    attributes: {
      "agentpgo.run.id": "run-1",
      "gen_ai.request.max_tokens": 64,
      "agentpgo.secret": "should-not-leak",
    },
  });

  const payload = toOtlpTracePayload([span], {
    serviceName: "checkout-agent",
    attributes: { "deployment.environment": "test" },
  });

  assert.equal(payload.resourceSpans.length, 1);
  assert.deepEqual(payload.resourceSpans[0].resource.attributes, [
    { key: "service.name", value: { stringValue: "checkout-agent" } },
    { key: "deployment.environment", value: { stringValue: "test" } },
    { key: "agentpgo.schema.version", value: { stringValue: AGENTPGO_SCHEMA_VERSION } },
  ]);
  const serialized = JSON.stringify(payload);
  assert.equal(serialized.includes("should-not-leak"), false);
  assert.equal(payload.resourceSpans[0].scopeSpans[0].spans[0].traceId, "a".repeat(32));
});

test("maps primitive and array attributes to OTLP values", () => {
  assert.deepEqual(attributesToOtlp({ answer: 3, ok: true, tags: ["a", "b"] }), [
    { key: "answer", value: { intValue: "3" } },
    { key: "ok", value: { boolValue: true } },
    { key: "tags", value: { arrayValue: { values: [{ stringValue: "a" }, { stringValue: "b" }] } } },
  ]);
});
