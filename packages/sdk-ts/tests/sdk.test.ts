import assert from "node:assert/strict";
import test from "node:test";
import { AgentPGOClient, init } from "../src/index.ts";

test("captures a span and flushes it as OTLP with bearer authentication", async () => {
  let request: Request | undefined;
  const client = new AgentPGOClient({
    apiKey: "key-123",
    endpoint: "https://collector.example/v1/traces",
    serviceName: "demo-agent",
    fetch: async (input, init) => {
      request = new Request(input, init);
      return new Response("{}", { status: 200 });
    },
  });

  const span = client.startSpan("agent.run", { attributes: { "agentpgo.run.id": "r-1" } });
  span.setAttribute("gen_ai.prompt", "do not send");
  span.end({ outputTokens: 4 });
  const result = await client.flush();

  assert.equal(result.sent, 1);
  assert.equal(request?.headers.get("authorization"), "Bearer key-123");
  assert.equal(request?.headers.get("content-type"), "application/json");
  assert.equal(JSON.stringify(await request!.json()).includes("do not send"), false);
});

test("trace helper ends spans on success and error without changing the return value", async () => {
  const client = init({ enabled: true, endpoint: "", fetch: async () => new Response("{}") });
  assert.equal(await client.trace("work", async () => 42), 42);
  await assert.rejects(() => client.trace("failing", async () => { throw new Error("boom"); }));
  await client.shutdown();
});


test("supports object trace options used by the documented SDK journey", async () => {
  const sent: any[] = [];
  const client = init({ enabled: true, endpoint: "", onExport: (spans) => sent.push(...spans) });
  const value = await client.trace({ node: "research", model: "openai/gpt-5.6-sol", run: async () => "answer" });
  assert.equal(value, "answer");
  assert.equal(sent[0].attributes["agentpgo.node"], "research");
  assert.equal(sent[0].attributes["gen_ai.request.model"], "openai/gpt-5.6-sol");
});
