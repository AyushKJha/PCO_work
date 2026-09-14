import { AgentPGOClient, type AgentPGOSpan } from "../../sdk-ts/src/index.ts";

export type GenerateText = (args: any) => Promise<any>;
export type AdapterOptions = { client: AgentPGOClient; model?: string; provider?: string; node?: string; spanName?: string };

function usageOf(result: any) {
  const usage = result?.usage ?? {};
  return {
    inputTokens: Number(usage.inputTokens ?? usage.promptTokens ?? usage.input_tokens ?? 0),
    outputTokens: Number(usage.outputTokens ?? usage.completionTokens ?? usage.output_tokens ?? 0),
  };
}

export function instrumentGenerateText(generateText: GenerateText, options: AdapterOptions): GenerateText {
  return async (args: any) => {
    const span = options.client.startSpan(options.spanName ?? (options.node ? `gen_ai.generate.${options.node}` : "gen_ai.generate"), {
      attributes: {
        "gen_ai.system": options.provider ?? "unknown",
        "gen_ai.request.model": options.model ?? "unknown",
        "agentpgo.node": options.node ?? "unknown",
      },
    });
    const started = Date.now();
    try {
      const result = await generateText(args);
      const usage = usageOf(result);
      span.end({ status: "ok", inputTokens: usage.inputTokens, outputTokens: usage.outputTokens, attributes: { "gen_ai.finish_reason": result?.finishReason ?? "unknown", "agentpgo.latency_ms": Date.now() - started } });
      return result;
    } catch (error) {
      span.end({ status: "error", statusMessage: error instanceof Error ? error.message : String(error), attributes: { "agentpgo.latency_ms": Date.now() - started } });
      throw error;
    }
  };
}

export function wrapLanguageModel<T extends { modelId?: string; doGenerate: (args: any) => Promise<any>; [key: string]: any }>(model: T, options: Omit<AdapterOptions, "model"> & Partial<Pick<AdapterOptions, "model">>): T {
  const original = model.doGenerate.bind(model);
  const wrapped = instrumentGenerateText(original, { ...options, model: options.model ?? model.modelId, spanName: "gen_ai.chat" });
  return Object.assign(Object.create(Object.getPrototypeOf(model)), model, { doGenerate: wrapped });
}

export type { AgentPGOClient, AgentPGOSpan };
