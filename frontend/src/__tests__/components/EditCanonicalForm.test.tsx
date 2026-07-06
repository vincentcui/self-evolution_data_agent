/* ════════════════════════════════════════════════════════════════════════════
 *  EditCanonicalForm — debounce 300ms 冲突预览 + reason 必填 + tier 切换
 * ══════════════════════════════════════════════════════════════════════════ */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import EditCanonicalForm from "@/components/audit/EditCanonicalForm";
import type { KnowledgeEntry } from "@/types";

vi.mock("@/api", () => ({
  editKnowledge: vi.fn().mockResolvedValue({}),
  previewConflict: vi.fn().mockResolvedValue({ conflicts: [] }),
}));

const entry: KnowledgeEntry = {
  id: 9,
  namespace_id: 1,
  entry_type: "rule",
  tier: "normal",
  content: "原内容",
  raw_input: "原内容",
  description: "",
  source: "manual",
  status: "canonical",
  is_superseded: false,
  refined_at: null,
  created_at: "2026-05-01T00:00:00Z",
} as unknown as KnowledgeEntry;

beforeEach(() => vi.clearAllMocks());

describe("EditCanonicalForm", () => {
  it("初始 content === entry.content → 不触发 previewConflict", async () => {
    const { previewConflict } = await import("@/api");
    render(<EditCanonicalForm entry={entry} />);
    // 等 ≥300ms 让 debounce 跑完
    await new Promise((r) => setTimeout(r, 350));
    expect(previewConflict).not.toHaveBeenCalled();
  });

  it("修改 content → 300ms 后调用 previewConflict", async () => {
    const { previewConflict } = await import("@/api");
    (previewConflict as any).mockResolvedValue({
      conflicts: [{ existing_id: 99, reason: "重复", suggested: "merge" }],
    });
    const user = userEvent.setup();
    render(<EditCanonicalForm entry={entry} />);
    const ta = screen.getByDisplayValue("原内容");
    await user.clear(ta);
    await user.type(ta, "新内容");
    await waitFor(
      () => expect(previewConflict).toHaveBeenCalled(),
      { timeout: 1500 },
    );
  });

  it("reason 留空 → 点保存提示 reason 必填", async () => {
    const { editKnowledge } = await import("@/api");
    const user = userEvent.setup();
    render(<EditCanonicalForm entry={entry} />);
    await user.click(screen.getByRole("button", { name: /^保.?存$/ }));
    expect(editKnowledge).not.toHaveBeenCalled();
  });

  it("reason 填写 → 调 editKnowledge + onDone", async () => {
    const { editKnowledge } = await import("@/api");
    const onDone = vi.fn();
    const user = userEvent.setup();
    render(<EditCanonicalForm entry={entry} onDone={onDone} />);
    const reason = screen.getByPlaceholderText("为何修改");
    await user.type(reason, "fix typo");
    await user.click(screen.getByRole("button", { name: /^保.?存$/ }));
    await waitFor(() =>
      expect(editKnowledge).toHaveBeenCalledWith(9, {
        content: "原内容",
        tier: "normal",
        reason: "fix typo",
      }),
    );
    await waitFor(() => expect(onDone).toHaveBeenCalled());
  });

  it("editKnowledge 抛错 → catch 兜底不崩", async () => {
    const { editKnowledge } = await import("@/api");
    (editKnowledge as any).mockRejectedValue({
      response: { data: { detail: "保存失败" } },
    });
    const user = userEvent.setup();
    render(<EditCanonicalForm entry={entry} />);
    const reason = screen.getByPlaceholderText("为何修改");
    await user.type(reason, "fix");
    await user.click(screen.getByRole("button", { name: /^保.?存$/ }));
    await waitFor(() => expect(editKnowledge).toHaveBeenCalled());
  });

  it("previewConflict 抛错 → silent 不崩 (catch 兜底)", async () => {
    const { previewConflict } = await import("@/api");
    (previewConflict as any).mockRejectedValue(new Error("net"));
    const user = userEvent.setup();
    render(<EditCanonicalForm entry={entry} />);
    const ta = screen.getByDisplayValue("原内容");
    await user.clear(ta);
    await user.type(ta, "改了");
    await waitFor(
      () => expect(previewConflict).toHaveBeenCalled(),
      { timeout: 1500 },
    );
    // 不抛, 组件依旧可见
    expect(screen.getByRole("button", { name: /^保.?存$/ })).toBeInTheDocument();
  });
});

describe("EditCanonicalForm — entry_type-specific panels", () => {
  const exampleEntry = {
    ...entry,
    id: 142,
    entry_type: "example",
    content: "统计品牌名称包含A级的品牌数量",
    payload: {
      question_pattern: "统计品牌名称包含A级的品牌数量",
      collections: ["shop_db.products"],
      join_keys: [],
      final_query_plan: {
        steps: [{ db_type: "mongodb", collection: "products", query: { pipeline: [{ $match: {} }] } }],
      },
      result_summary: "按名称过滤统计",
      // legacy
      question: "统计品牌名称包含A级的品牌数量",
      target_collection: "products",
      target_database: "shop_db",
      query_json: { pipeline: [{ $match: {} }] },
    },
  } as unknown as KnowledgeEntry;

  const routeHintEntry = {
    ...entry,
    id: 143,
    entry_type: "route_hint",
    content: "某商品的订单数量",
    payload: {
      collection_path: ["c_product", "c_category_group"],
      join_fields: [],
      cost_strategy: "default",
      reason: "测试理由",
    },
  } as unknown as KnowledgeEntry;

  it("example 类型挂 ExampleEditPanel", () => {
    render(<EditCanonicalForm entry={exampleEntry} />);
    // ExampleEditPanel 特征 label
    expect(screen.getByText(/涉及集合/)).toBeInTheDocument();
    expect(screen.getByText(/查询计划/)).toBeInTheDocument();
  });

  it("route_hint 类型挂 RouteHintEditPanel", () => {
    render(<EditCanonicalForm entry={routeHintEntry} />);
    expect(screen.getByText(/集合路径/)).toBeInTheDocument();
    expect(screen.getByText(/路径理由/)).toBeInTheDocument();
  });
});
