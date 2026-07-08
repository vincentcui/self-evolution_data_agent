import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

// 可变角色 — vi.hoisted 保证在 vi.mock 工厂里安全引用
const h = vi.hoisted(() => ({ role: "super_admin" }));

vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({ user: { role: h.role, username: "u" }, logout: vi.fn() }),
}));
vi.mock("@/hooks/useSessions", () => ({
  useSessions: () => ({
    sessions: [], activeSessionId: null, setActiveSessionId: vi.fn(),
    createSession: vi.fn(), renameSession: vi.fn(), deleteSession: vi.fn(),
    loading: false, refresh: vi.fn(),
  }),
}));
vi.mock("@/hooks/useReadiness", () => ({ useReadiness: () => ({ ready: true }) }));

import Layout from "@/components/Layout";

const renderAs = (role: string) => {
  h.role = role;
  return render(<MemoryRouter><Layout /></MemoryRouter>);
};

describe("首页配置中心入口 (super_admin)", () => {
  it("super_admin → 侧栏显示「配置中心」", () => {
    renderAs("super_admin");
    expect(screen.getByText("配置中心")).toBeTruthy();
  });

  it("admin → 不显示「配置中心」，但「工作台」仍在", () => {
    renderAs("admin");
    expect(screen.queryByText("配置中心")).toBeNull();
    expect(screen.getByText("工作台")).toBeTruthy();
  });

  it("user → 不显示「配置中心」", () => {
    renderAs("user");
    expect(screen.queryByText("配置中心")).toBeNull();
  });
});
