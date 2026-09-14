import assert from "node:assert/strict";
import test from "node:test";
import { AgentPGOClient } from "../../sdk-ts/src/index.ts";
import { instrumentGenerateText, wrapLanguageModel } from "../src/index.ts";

function recordingClient() {
  const sent: unknown[] = [];
  const client = new AgentPGOClient({ enabled: true, endpoint: "", onExport: (spans) => sent.push(...spans) });
  return { client, sent };
}

test("instruments generateText and records model usage without recording prompts", async () => {
  const { client, sent } = recordingClient();
  const generateText = async (_args: unknown) => ({ text: "done", usage: { promptTokens: 3, completionTokens: 2 } });
  const instrumented = instrumentGenerateText(generateText, { client, model: "gpt-test" });
  const output = await instrumented({ prompt: "secret prompt" });
  assert.equal(output.text, "done");
  assert.equal(sent.length, 1);
  const span = sent[0] as { attributes: Record<string, unknown> };
  assert.equal(span.attributes["gen_ai.request.model"], "gpt-test");
  assert.equal(span.attributes["gen_ai.usage.input_tokens"], 3);
  assert.equal(JSON.stringify(span).includes("secret prompt"), false);
});

test("wraps a language model doGenerate call and preserves the model result", async () => {
  const { client, sent } = recordingClient();
  const model = { specificationVersion: "v1", modelId: "mock", doGenerate: async () => ({ text: "ok", usage: { promptTokens: 1, completionTokens: 1 } }) };
  const wrapped = wrapLanguageModel(model, { client });
  const result = await wrapped.doGenerate({ prompt: [{ role: "user", content: [{ type: "text", text: "secret" }] }] });
  assert.equal(result.text, "ok");
  assert.equal(sent.length, 1);
  assert.equal((sent[0] as { name: string }).name, "gen_ai.chat");
});
