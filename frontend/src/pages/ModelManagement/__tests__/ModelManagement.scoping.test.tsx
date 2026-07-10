import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

const listModelConfigs = vi.hoisted(() => vi.fn().mockResolvedValue([]));
vi.mock("@/api/modelConfig", () => ({
  listModelConfigs,
  activateModelConfig: vi.fn(),
  deleteModelConfig: vi.fn(),
  testModelConnection: vi.fn(),
}));
vi.mock("@/api", () => ({ fetchNamespaces: vi.fn().mockResolvedValue([]) }));
vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({ user: { role: "super_admin" } }),
}));

let outletCtx: unknown = null;
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useOutletContext: () => outletCtx,
}));

import ModelManagement from "../index";

describe("ModelManagement list 作用域", () => {
  beforeEach(() => listModelConfigs.mockClear());

  it("workspace 语境(有 activeNs) → 按空间 id 过滤", async () => {
    outletCtx = { activeNs: { id: 7, name: "A" }, loading: false, refresh: vi.fn() };
    render(<ModelManagement />);
    await waitFor(() => expect(listModelConfigs).toHaveBeenCalledWith(7));
  });

  it("配置中心语境(outlet 为 null) → 拉全量(无参)", async () => {
    outletCtx = null;
    render(<ModelManagement />);
    await waitFor(() => expect(listModelConfigs).toHaveBeenCalledWith(undefined));
  });
});

describe("ModelManagement 新增配置按钮禁用态", () => {
  beforeEach(() => listModelConfigs.mockClear());

  const addBtn = () =>
    screen.getByRole("button", { name: /新增配置/ }) as HTMLButtonElement;

  it("workspace 语境 activeNs 未就绪(加载中) → 新增配置禁用", async () => {
    outletCtx = { activeNs: null, loading: true, refresh: () => {} };
    render(<ModelManagement />);
    await waitFor(() => expect(listModelConfigs).toHaveBeenCalled());
    expect(addBtn().disabled).toBe(true);
  });

  it("workspace 语境 activeNs 已就绪 → 新增配置可用", async () => {
    outletCtx = { activeNs: { id: 7, name: "A" }, loading: false, refresh: () => {} };
    render(<ModelManagement />);
    await waitFor(() => expect(listModelConfigs).toHaveBeenCalledWith(7));
    expect(addBtn().disabled).toBe(false);
  });

  it("配置中心语境(outlet 为 null) → 新增配置可用", async () => {
    outletCtx = null;
    render(<ModelManagement />);
    await waitFor(() => expect(listModelConfigs).toHaveBeenCalledWith(undefined));
    expect(addBtn().disabled).toBe(false);
  });
});
