import { describe, it, expect } from "vitest";
import {
  protocolForProvider,
  resolveModelTypeForProvider,
  isEmbeddingAllowed,
} from "../modelFormUtils";

describe("protocolForProvider", () => {
  it("anthropic provider → protocol=anthropic", () => {
    expect(protocolForProvider("anthropic")).toBe("anthropic");
    expect(protocolForProvider("Anthropic")).toBe("anthropic"); // case-insensitive
  });

  it("custom provider → trusts current protocol", () => {
    expect(protocolForProvider("custom", "anthropic")).toBe("anthropic");
    expect(protocolForProvider("custom", "openai")).toBe("openai");
  });

  it("custom provider defaults to openai when no current provided", () => {
    expect(protocolForProvider("custom")).toBe("openai");
  });

  it("custom provider ignores invalid protocol values", () => {
    // @ts-expect-error — 故意传非法值，验证安全兜底
    expect(protocolForProvider("custom", "invalid")).toBe("openai");
  });

  it("other providers → always openai", () => {
    expect(protocolForProvider("deepseek")).toBe("openai");
    expect(protocolForProvider("openai")).toBe("openai");
    expect(protocolForProvider("qwen")).toBe("openai");
    expect(protocolForProvider("siliconflow")).toBe("openai");
    expect(protocolForProvider("zhipu")).toBe("openai");
  });
});

describe("resolveModelTypeForProvider", () => {
  it("anthropic → 强制 CHAT，忽略当前值", () => {
    expect(resolveModelTypeForProvider("anthropic", "CHAT")).toBe("CHAT");
    expect(resolveModelTypeForProvider("anthropic", "EMBEDDING")).toBe("CHAT");
  });

  it("非 anthropic → 保留当前 model_type", () => {
    expect(resolveModelTypeForProvider("openai", "CHAT")).toBe("CHAT");
    expect(resolveModelTypeForProvider("openai", "EMBEDDING")).toBe("EMBEDDING");
    expect(resolveModelTypeForProvider("custom", "EMBEDDING")).toBe("EMBEDDING");
  });
});

describe("isEmbeddingAllowed", () => {
  it("anthropic 不允许 EMBEDDING", () => {
    expect(isEmbeddingAllowed("anthropic")).toBe(false);
  });

  it("其他 provider 允许 EMBEDDING", () => {
    expect(isEmbeddingAllowed("openai")).toBe(true);
    expect(isEmbeddingAllowed("deepseek")).toBe(true);
    expect(isEmbeddingAllowed("custom")).toBe(true);
    expect(isEmbeddingAllowed("qwen")).toBe(true);
  });
});
