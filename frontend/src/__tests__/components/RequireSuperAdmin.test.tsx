import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";

const h = vi.hoisted(() => ({ role: "super_admin", loading: false }));
vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({ user: { role: h.role }, loading: h.loading }),
}));

import RequireSuperAdmin from "@/components/RequireSuperAdmin";

const renderGuard = () =>
  render(
    <MemoryRouter initialEntries={["/config"]}>
      <Routes>
        <Route element={<RequireSuperAdmin />}>
          <Route path="/config" element={<div>CONFIG</div>} />
        </Route>
        <Route path="/" element={<div>HOME</div>} />
      </Routes>
    </MemoryRouter>
  );

describe("RequireSuperAdmin", () => {
  it("super_admin → 放行子路由", () => {
    h.role = "super_admin"; h.loading = false;
    renderGuard();
    expect(screen.getByText("CONFIG")).toBeTruthy();
  });

  it("admin → 重定向到首页", () => {
    h.role = "admin"; h.loading = false;
    renderGuard();
    expect(screen.getByText("HOME")).toBeTruthy();
    expect(screen.queryByText("CONFIG")).toBeNull();
  });

  it("loading → 既不放行也不重定向 (渲染 null)", () => {
    h.role = "user"; h.loading = true; // 即便非 super_admin, loading 期间也先返回 null
    const { container } = renderGuard();
    expect(container.textContent).toBe("");
    expect(screen.queryByText("CONFIG")).toBeNull();
    expect(screen.queryByText("HOME")).toBeNull();
  });
});
