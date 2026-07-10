import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor } from "@testing-library/react";

const listModelConfigs = vi.hoisted(() => vi.fn().mockResolvedValue([]));
const modelFormSpy = vi.hoisted(() => vi.fn());

vi.mock("@/api/modelConfig", () => ({
  listModelConfigs,
  activateModelConfig: vi.fn(),
  deleteModelConfig: vi.fn(),
  testModelConnection: vi.fn(),
}));
vi.mock("@/api", () => ({ fetchNamespaces: vi.fn().mockResolvedValue([]) }));
vi.mock("@/context/AuthContext", () => ({
  // 非 super_admin: 若不下发 namespaceId, 提交 CHAT 时 namespace_id=null → 后端 403 死路
  useAuth: () => ({ user: { role: "admin" } }),
}));

let outletCtx: unknown = null;
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useOutletContext: () => outletCtx,
}));

// 用 spy 记录 ModelForm 收到的 props, 渲染 null 避免真实表单开销。
// ModelForm 恒在树中 (open={formOpen}), 挂载即捕获 props, 无需点击。
vi.mock("../ModelForm", () => ({
  default: (props: Record<string, unknown>) => {
    modelFormSpy(props);
    return null;
  },
}));

import ModelManagement from "../index";

const lastProps = () =>
  modelFormSpy.mock.calls.at(-1)![0] as Record<string, unknown>;

describe("ModelManagement → ModelForm 命名空间绑定 (D2 死路修复)", () => {
  beforeEach(() => {
    modelFormSpy.mockClear();
    listModelConfigs.mockClear();
  });

  it("workspace 语境(有 activeNs) → ModelForm 收到 namespaceId=空间 id (锁定)", async () => {
    outletCtx = { activeNs: { id: 7, name: "A" }, loading: false, refresh: vi.fn() };
    render(<ModelManagement />);
    await waitFor(() => expect(modelFormSpy).toHaveBeenCalled());
    expect(lastProps().namespaceId).toBe(7);
    expect(lastProps().namespaceName).toBe("A");
  });

  it("配置中心语境(outlet 为 null) → ModelForm 收到 namespaceId=undefined (不锁定)", async () => {
    outletCtx = null;
    render(<ModelManagement />);
    await waitFor(() => expect(modelFormSpy).toHaveBeenCalled());
    expect(lastProps().namespaceId).toBeUndefined();
  });
});
