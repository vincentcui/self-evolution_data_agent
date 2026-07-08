import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";

const h = vi.hoisted(() => ({ role: "super_admin", loading: false }));
vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({ user: { role: h.role }, loading: h.loading }),
}));

import RequireSuperAdmin from "../RequireSuperAdmin";

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
});
