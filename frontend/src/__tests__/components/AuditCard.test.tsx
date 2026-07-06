/* ════════════════════════════════════════════════════════════════════════════
 *  AuditCard 单测 — proposed/canonical/rejected 三态 + 5 个动作分支
 * ----------------------------------------------------------------------------
 *  覆盖目标:
 *    • status=proposed  → "通过" / "拒绝" + 审计日志按钮
 *    • status=canonical → "编辑" / "下架" + 审计日志按钮
 *    • status=rejected  → "恢复" + 审计日志按钮
 *    • Modal 渲染分支 (审计日志 / 编辑表单)
 *    • selectable+onSelect checkbox 行为
 *    • 各 action handler 错误兜底 (catch + message.error)
 * ══════════════════════════════════════════════════════════════════════════ */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import AuditCard from "@/components/audit/AuditCard";
import type { KnowledgeEntry } from "@/types";

vi.mock("@/api", () => ({
  approveEntry: vi.fn().mockResolvedValue({}),
  rejectEntry: vi.fn().mockResolvedValue({}),
  restoreEntry: vi.fn().mockResolvedValue({}),
  deleteKnowledgeWithMode: vi.fn().mockResolvedValue({}),
  fetchAuditLog: vi.fn().mockResolvedValue([]),
  editKnowledge: vi.fn().mockResolvedValue({}),
  previewConflict: vi.fn().mockResolvedValue({ conflicts: [] }),
  // ── Phase 3 Task 3.2 — terminology mode 编辑表单数据源 ──
  getDatabases: vi.fn().mockResolvedValue({ databases: [] }),
  getCollections: vi.fn().mockResolvedValue({ database: "", db_type: null, collections: [] }),
}));

const baseEntry: KnowledgeEntry = {
  id: 7,
  namespace_id: 1,
  entry_type: "terminology",
  tier: "normal",
  content: "订单=c_product",
  raw_input: "订单=c_product",
  description: "术语描述",
  source: "manual",
  status: "proposed",
  is_superseded: false,
  refined_at: null,
  created_at: "2026-05-01T00:00:00Z",
} as unknown as KnowledgeEntry;

beforeEach(() => vi.clearAllMocks());

describe("AuditCard — status branches", () => {
  it("proposed 渲染通过/拒绝按钮 + 审计日志按钮", () => {
    render(<AuditCard entry={baseEntry} />);
    expect(screen.getByRole("button", { name: /^通.?过$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^拒.?绝$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /审计日志/ })).toBeInTheDocument();
  });

  it("canonical 渲染编辑/下架按钮 (无通过/拒绝)", () => {
    render(<AuditCard entry={{ ...baseEntry, status: "canonical" }} />);
    expect(screen.getByRole("button", { name: /^编.?辑$/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^下.?架$/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^通.?过$/ })).toBeNull();
  });

  it("rejected 渲染恢复按钮", () => {
    render(<AuditCard entry={{ ...baseEntry, status: "rejected" }} />);
    expect(screen.getByRole("button", { name: /^恢.?复$/ })).toBeInTheDocument();
  });

  it("description 存在时渲染副文案", () => {
    render(<AuditCard entry={baseEntry} />);
    expect(screen.getByText("术语描述")).toBeInTheDocument();
  });

  it("selectable 模式渲染 Checkbox, 触发 onSelect", async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();
    render(
      <AuditCard entry={baseEntry} selectable selected={false} onSelect={onSelect} />,
    );
    const cb = document.querySelector(".ant-checkbox-input") as HTMLInputElement;
    await user.click(cb);
    expect(onSelect).toHaveBeenCalledWith(true);
  });
});

describe("AuditCard — action handlers (success path)", () => {
  it("approve 调用 onAction 回调", async () => {
    const onAction = vi.fn();
    const user = userEvent.setup();
    const api = await import("@/api");
    render(<AuditCard entry={baseEntry} onAction={onAction} />);
    await user.click(screen.getByRole("button", { name: /^通.?过$/ }));
    await waitFor(() => expect(api.approveEntry).toHaveBeenCalledWith(7));
    await waitFor(() => expect(onAction).toHaveBeenCalled());
  });

  it("approve API 抛错时不崩 (走 catch 分支)", async () => {
    const api = await import("@/api");
    (api.approveEntry as any).mockRejectedValueOnce({
      response: { data: { detail: "boom" } },
    });
    const user = userEvent.setup();
    render(<AuditCard entry={baseEntry} />);
    await user.click(screen.getByRole("button", { name: /^通.?过$/ }));
    await waitFor(() => expect(api.approveEntry).toHaveBeenCalled());
  });

  it("restore: prompt 返 null 直接 return (rejected)", async () => {
    const promptSpy = vi.spyOn(window, "prompt").mockReturnValue(null);
    const api = await import("@/api");
    const user = userEvent.setup();
    render(<AuditCard entry={{ ...baseEntry, status: "rejected" }} />);
    await user.click(screen.getByRole("button", { name: /^恢.?复$/ }));
    expect(promptSpy).toHaveBeenCalled();
    expect(api.restoreEntry).not.toHaveBeenCalled();
    promptSpy.mockRestore();
  });

  it("restore: prompt 给 reason → 调 restoreEntry", async () => {
    const promptSpy = vi.spyOn(window, "prompt").mockReturnValue("误删恢复");
    const api = await import("@/api");
    const onAction = vi.fn();
    const user = userEvent.setup();
    render(
      <AuditCard entry={{ ...baseEntry, status: "rejected" }} onAction={onAction} />,
    );
    await user.click(screen.getByRole("button", { name: /^恢.?复$/ }));
    await waitFor(() => expect(api.restoreEntry).toHaveBeenCalledWith(7, "误删恢复"));
    promptSpy.mockRestore();
  });

  it("softDelete: prompt 给 reason → 调 deleteKnowledgeWithMode soft", async () => {
    const promptSpy = vi.spyOn(window, "prompt").mockReturnValue("过期");
    const api = await import("@/api");
    const user = userEvent.setup();
    render(<AuditCard entry={{ ...baseEntry, status: "canonical" }} />);
    await user.click(screen.getByRole("button", { name: /^下.?架$/ }));
    await waitFor(() =>
      expect(api.deleteKnowledgeWithMode).toHaveBeenCalledWith(7, "soft", "过期"),
    );
    promptSpy.mockRestore();
  });

  it("softDelete: prompt 返 null → 不调 API", async () => {
    const promptSpy = vi.spyOn(window, "prompt").mockReturnValue(null);
    const api = await import("@/api");
    const user = userEvent.setup();
    render(<AuditCard entry={{ ...baseEntry, status: "canonical" }} />);
    await user.click(screen.getByRole("button", { name: /^下.?架$/ }));
    expect(api.deleteKnowledgeWithMode).not.toHaveBeenCalled();
    promptSpy.mockRestore();
  });
});

describe("AuditCard — modals", () => {
  it("点击 审计日志 → 打开 Modal", async () => {
    const user = userEvent.setup();
    render(<AuditCard entry={baseEntry} />);
    await user.click(screen.getByRole("button", { name: /审计日志/ }));
    await waitFor(() => expect(screen.getByText("审计时间线")).toBeInTheDocument());
  });

  it("canonical 点击 编辑 → 打开编辑 Modal", async () => {
    const user = userEvent.setup();
    render(<AuditCard entry={{ ...baseEntry, status: "canonical" }} />);
    await user.click(screen.getByRole("button", { name: /^编.?辑$/ }));
    await waitFor(() =>
      expect(screen.getByText("编辑知识条目")).toBeInTheDocument(),
    );
  });
});

describe("AuditCard — entry_type type-specific blocks", () => {
  const examplePayload = {
    question_pattern: "某商品的订单数量",
    final_pipeline: { type: "execute_plan", steps: [] },
    chart_type: "bar",
    field_mappings: [{ collection: "c_product", field: "categoryId" }],
    collections: ["c_product", "c_category_group"],
    tool_count: 5,
  };
  const exampleEntry: KnowledgeEntry = {
    ...baseEntry,
    id: 132,
    entry_type: "example",
    content: "某商品的订单数量",
    payload: examplePayload as unknown as Record<string, unknown>,
  } as unknown as KnowledgeEntry;

  const routeHintPayload = {
    collection_path: ["c_product", "c_category_group"],
    join_fields: [{ a: "c_product.categoryId", b: "c_category_group._id" }],
    cost_strategy: "default",
    reason: "商品→订单两层关联",
  };
  const routeHintEntry: KnowledgeEntry = {
    ...baseEntry,
    id: 133,
    entry_type: "route_hint",
    content: "某商品的订单数量",
    payload: routeHintPayload as unknown as Record<string, unknown>,
  } as unknown as KnowledgeEntry;

  it("example 类型渲染 collections / chart_type / field_mappings", () => {
    render(<AuditCard entry={exampleEntry} />);
    expect(screen.getAllByText(/c_product/).length).toBeGreaterThan(0);
    expect(screen.getByText("c_category_group")).toBeInTheDocument();
    expect(screen.getByText(/chart: bar/)).toBeInTheDocument();
    expect(screen.getByText(/tools: 5/)).toBeInTheDocument();
    expect(screen.getByText("c_product.categoryId")).toBeInTheDocument();
  });

  it("route_hint 类型渲染 collection_path / join_fields / reason", () => {
    render(<AuditCard entry={routeHintEntry} />);
    expect(screen.getByText("c_product.categoryId ↔ c_category_group._id")).toBeInTheDocument();
    expect(screen.getByText("商品→订单两层关联")).toBeInTheDocument();
    expect(screen.getByText(/策略: default/)).toBeInTheDocument();
  });

  it("entry_type Tag 颜色按 ENTRY_TYPE_COLORS 上色", () => {
    const { container } = render(<AuditCard entry={exampleEntry} />);
    const tags = Array.from(container.querySelectorAll(".ant-tag"));
    const exampleTag = tags.find((el) => el.textContent === "示例查询");
    expect(exampleTag).toBeTruthy();
    expect(exampleTag!.className).toMatch(/ant-tag-green/);
  });
});
