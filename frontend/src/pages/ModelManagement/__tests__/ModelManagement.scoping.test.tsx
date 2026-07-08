import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, waitFor } from "@testing-library/react";

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
