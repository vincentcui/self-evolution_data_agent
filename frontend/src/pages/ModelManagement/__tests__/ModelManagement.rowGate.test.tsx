import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

const globalCfg = vi.hoisted(() => ({
  id: 1, provider: "openai", base_url: "https://x", api_key: "****",
  model_name: "g", model_type: "CHAT", protocol: "openai",
  namespace_id: null, namespace_name: null, temperature: 0, max_tokens: 12288,
  max_history_turns: 5, is_active: false, completions_path: null, embeddings_path: null,
}));
vi.mock("@/api/modelConfig", () => ({
  listModelConfigs: vi.fn().mockResolvedValue([globalCfg]),
  activateModelConfig: vi.fn(), deleteModelConfig: vi.fn(), testModelConnection: vi.fn(),
}));
vi.mock("@/api", () => ({ fetchNamespaces: vi.fn().mockResolvedValue([]) }));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: { role: "admin" } }) }));
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useOutletContext: () => null,
}));

import ModelManagement from "../index";

const btn = (name: string) => screen.getByRole("button", { name }) as HTMLButtonElement;

describe("ModelManagement 全局配置行写操作门槛 (N2/D2)", () => {
  it("普通 admin → 全局行 连接测试/激活/编辑/删除 全部禁用", async () => {
    render(<ModelManagement />);
    await waitFor(() => screen.getByText("g"));
    expect(btn("连接测试").disabled).toBe(true);
    expect(btn("激活").disabled).toBe(true);
    expect(btn("编辑").disabled).toBe(true);
    expect(btn("删除").disabled).toBe(true);
  });
});
