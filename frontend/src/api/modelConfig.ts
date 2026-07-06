/* ════════════════════════════════════════════
 *  模型配置管理 API — 对接 /api/model-config/*
 *  使用 api/index.ts 统一 http 实例（含 401 自动跳登录、60s timeout）
 * ════════════════════════════════════════════ */

import { http } from "./index";

export type ModelType     = "CHAT" | "EMBEDDING";
export type ModelProtocol = "openai" | "anthropic";

export interface ModelConfig {
  id?: number;
  provider: string;
  protocol?: ModelProtocol;
  base_url: string;
  api_key: string;
  model_name: string;
  model_type: ModelType;
  temperature?: number | null;
  max_tokens?: number | null;
  is_active?: boolean;
  completions_path?: string | null;
  embeddings_path?: string | null;
  proxy_enabled?: boolean;
  proxy_host?: string | null;
  proxy_port?: number | null;
  proxy_username?: string | null;
  proxy_password?: string | null;
  created_at?: string;
  updated_at?: string | null;
}

export interface ModelConfigUpdate extends ModelConfig {
  id: number;
}

export interface CheckReadyResult {
  chat_model_ready: boolean;
  embedding_model_ready: boolean;
  ready: boolean;
}

/** 获取全部模型配置（API Key 脱敏）*/
export const listModelConfigs = () =>
  http.get<ModelConfig[]>("/model-config/list").then((r) => r.data);

/** 新增配置（不自动激活）*/
export const addModelConfig = (cfg: Omit<ModelConfig, "id" | "is_active" | "created_at" | "updated_at">) =>
  http.post<ModelConfig>("/model-config/add", cfg).then((r) => r.data);

/** 更新配置（API Key 传 **** 则跳过更新）*/
export const updateModelConfig = (cfg: ModelConfigUpdate) =>
  http.put<ModelConfig>("/model-config/update", cfg).then((r) => r.data);

/** 逻辑删除 */
export const deleteModelConfig = (id: number) =>
  http.delete(`/model-config/${id}`);

/** 激活并热切换 */
export const activateModelConfig = (id: number) =>
  http.post<ModelConfig>(`/model-config/activate/${id}`).then((r) => r.data);

/** 测试连接（不入库）.
 *  编辑已有配置时传入 id，后端检测到 key 被打码可从 DB 取真实值。
 */
export const testModelConnection = (
  cfg: Omit<ModelConfig, "is_active" | "created_at" | "updated_at">,
) =>
  http
    .post<{ success: boolean; message: string }>("/model-config/test", cfg)
    .then((r) => r.data);

/** 检查 Chat + Embedding 是否都已激活 */
export const checkModelReady = () =>
  http.get<CheckReadyResult>("/model-config/check-ready").then((r) => r.data);
