/**
 * 激活保护逻辑单元测试
 * 测试 Embedding 激活前置检查规则（纯逻辑，不依赖 React）
 */
import { describe, it, expect } from "vitest";
import type { ModelConfig } from "@/api/modelConfig";
import { hasOtherActiveEmbedding, isEmbeddingEditLocked } from "../modelFormUtils";

// ── 测试数据 ──────────────────────────────────────────────────
const makeConfig = (
  id: number,
  type: "CHAT" | "EMBEDDING",
  is_active: boolean,
): ModelConfig => ({
  id,
  provider: "openai",
  protocol: "openai",
  base_url: "https://example.com",
  api_key: "sk-****",
  model_name: `model-${id}`,
  model_type: type,
  is_active,
});

// ── 激活保护 ──────────────────────────────────────────────────
describe("Embedding 激活保护", () => {
  it("无其他 active Embedding 时允许激活", () => {
    const configs = [
      makeConfig(1, "EMBEDDING", false),
      makeConfig(2, "CHAT", true),
    ];
    expect(hasOtherActiveEmbedding(configs, 1)).toBe(false);
  });

  it("已有其他 active Embedding 时拦截激活", () => {
    const configs = [
      makeConfig(1, "EMBEDDING", false), // 目标
      makeConfig(2, "EMBEDDING", true),  // 已激活的另一个
    ];
    expect(hasOtherActiveEmbedding(configs, 1)).toBe(true);
  });

  it("自身已是 active Embedding 不视为冲突", () => {
    const configs = [
      makeConfig(1, "EMBEDDING", true), // 已激活自身
    ];
    expect(hasOtherActiveEmbedding(configs, 1)).toBe(false);
  });

  it("hasOtherActiveEmbedding 对 CHAT 类型目标也正确返回——存在其他 active EMBEDDING 时返回 true", () => {
    const configs = [
      makeConfig(1, "CHAT", false),
      makeConfig(2, "EMBEDDING", true),
    ];
    // hasOtherActiveEmbedding 是纯谓词, 不按 model_type 过滤目标 —
    // 调用方 (activation guard) 先判 model_type==='EMBEDDING' 再调本函数,
    // 所以 CHAT 调用它返回 true 也无害 (根本走不到)。
    expect(hasOtherActiveEmbedding(configs, 1)).toBe(true);
  });
});

// ── 编辑/删除锁定 ─────────────────────────────────────────────
describe("active Embedding 编辑/删除锁定", () => {
  it("active Embedding 配置被锁定", () => {
    const cfg = makeConfig(1, "EMBEDDING", true);
    expect(isEmbeddingEditLocked(cfg)).toBe(true);
  });

  it("inactive Embedding 配置不被锁定", () => {
    const cfg = makeConfig(1, "EMBEDDING", false);
    expect(isEmbeddingEditLocked(cfg)).toBe(false);
  });

  it("active Chat 配置不被锁定", () => {
    const cfg = makeConfig(1, "CHAT", true);
    expect(isEmbeddingEditLocked(cfg)).toBe(false);
  });

  it("inactive Chat 配置不被锁定", () => {
    const cfg = makeConfig(1, "CHAT", false);
    expect(isEmbeddingEditLocked(cfg)).toBe(false);
  });
});
