/**
 * ModelForm 纯逻辑工具函数 — 独立于 React 组件，便于单元测试。
 */
import type { ModelConfig, ModelProtocol } from "@/api/modelConfig";

/**
 * 根据 provider 和用户显式选择推导协议标识。
 * - anthropic  → 强制 "anthropic"
 * - custom     → 信任 current（允许接 Claude 兼容端点）
 * - 其他       → 强制 "openai"
 */
export function protocolForProvider(
  provider: string,
  current: ModelProtocol = "openai",
): ModelProtocol {
  const p = provider.toLowerCase();
  if (p === "anthropic") return "anthropic";
  if (p === "custom") return (["openai", "anthropic"] as ModelProtocol[]).includes(current) ? current : "openai";
  return "openai";
}

/**
 * 选择 anthropic provider 时，模型类型必须强制为 CHAT。
 * （Anthropic 协议首期不支持 Embedding）
 */
export function resolveModelTypeForProvider(
  provider: string,
  current: "CHAT" | "EMBEDDING",
): "CHAT" | "EMBEDDING" {
  return provider === "anthropic" ? "CHAT" : current;
}

/**
 * 是否允许选择 EMBEDDING 类型（Anthropic 时禁止）。
 */
export function isEmbeddingAllowed(provider: string): boolean {
  return provider !== "anthropic";
}

/**
 * 是否存在另一条已激活的 Embedding 配置 (激活互斥前置检查)。
 */
export function hasOtherActiveEmbedding(
  configs: ModelConfig[],
  targetId: number,
): boolean {
  return configs.some(
    (c) => c.model_type === "EMBEDDING" && c.is_active && c.id !== targetId,
  );
}

/**
 * 已激活的 Embedding 配置是否被锁定 (编辑/删除会破坏知识库索引)。
 */
export function isEmbeddingEditLocked(cfg: ModelConfig): boolean {
  return cfg.model_type === "EMBEDDING" && !!cfg.is_active;
}
