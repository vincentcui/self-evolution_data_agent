/* ════════════════════════════════════════════════════════════════════════════
 *  HypotheticalQueriesPanel 单测 — 渲染展示 + 编辑全部入口迁移到 AuditCard 后的 PUT body 契约
 *
 *  入口已从 panel 内部迁移到 AuditCard 右侧操作区（ref.openEdit），
 *  故「编辑全部」按钮 + PUT body 测试改为渲染 AuditCard 的 rule 场景。
 * ══════════════════════════════════════════════════════════════════════════ */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { HypotheticalQueriesPanel } from "@/components/audit/HypotheticalQueriesPanel";
import AuditCard from "@/components/audit/AuditCard";
import type { KnowledgeEntry } from "@/types";

const mockEditKnowledge = vi.fn().mockResolvedValue({
  entry: { id: 42 },
  conflicts: [],
});

vi.mock("@/api", () => ({
  editKnowledge: (...args: unknown[]) => mockEditKnowledge(...args),
  approveEntry: vi.fn().mockResolvedValue({}),
  rejectEntry: vi.fn().mockResolvedValue({}),
  restoreEntry: vi.fn().mockResolvedValue({}),
  deleteKnowledgeWithMode: vi.fn().mockResolvedValue({}),
}));

const HQ_JSON = JSON.stringify([
  { q: "问题1", generated_at: "2026-01-01", model: "qwen-plus" },
  { q: "问题2", generated_at: "2026-01-01", model: "qwen-plus" },
]);

describe("HypotheticalQueriesPanel", () => {
  beforeEach(() => {
    mockEditKnowledge.mockClear();
  });

  it("renders HQ tags from hypothetical_queries_json", () => {
    render(
      <HypotheticalQueriesPanel
        entryId={42}
        hypothetical_queries_json={HQ_JSON}
      />,
    );
    expect(screen.getByText("问题1")).toBeInTheDocument();
    expect(screen.getByText("问题2")).toBeInTheDocument();
  });

  it("renders empty state when no HQ", () => {
    render(
      <HypotheticalQueriesPanel
        entryId={42}
        hypothetical_queries_json="[]"
      />,
    );
    expect(screen.getByText(/未生成假设触发问题/)).toBeInTheDocument();
  });
});

describe("AuditCard 编辑全部（HQ 入口迁移后）", () => {
  beforeEach(() => {
    mockEditKnowledge.mockClear();
  });

  const ruleEntry: KnowledgeEntry = {
    id: 42,
    namespace_id: 1,
    entry_type: "rule",
    tier: "normal",
    content: "查询订单默认按时间倒序",
    raw_input: "",
    description: "",
    source: "manual",
    status: "proposed",
    is_superseded: false,
    payload: null,
    refined_at: null,
    created_at: "2026-07-06T10:00:00",
    hypothetical_queries_json: JSON.stringify([
      { q: "旧问题1", generated_at: "2026-01-01", model: "qwen-plus" },
    ]),
  };

  it("rule 类型右侧操作区显示「编辑全部」按钮", () => {
    render(<AuditCard entry={ruleEntry} />);
    expect(screen.getByRole("button", { name: "编辑全部" })).toBeInTheDocument();
  });

  it("点击「编辑全部」→ 修改 → PUT body 含 hypothetical_queries + reason", async () => {
    const onAction = vi.fn();
    render(<AuditCard entry={ruleEntry} onAction={onAction} />);

    // 点击右侧「编辑全部」
    await userEvent.click(screen.getByRole("button", { name: "编辑全部" }));

    // antd Modal 渲染到 document.body portal
    await waitFor(() => {
      expect(document.querySelector(".ant-modal")).toBeInTheDocument();
    });

    // textarea 应含旧内容
    const textarea = document.querySelector(".ant-modal textarea") as HTMLTextAreaElement;
    expect(textarea).toBeTruthy();
    expect(textarea.value).toBe("旧问题1");

    // 清空并输入新内容
    await userEvent.clear(textarea);
    await userEvent.type(textarea, "新问题1\n新问题2");

    // 找 Modal footer 的 OK 按钮
    const modalFooter = document.querySelector(".ant-modal-footer");
    const okBtn = modalFooter?.querySelector(".ant-btn-primary") as HTMLButtonElement;
    expect(okBtn).toBeTruthy();
    await userEvent.click(okBtn);

    await waitFor(() => {
      expect(mockEditKnowledge).toHaveBeenCalledWith(42, {
        hypothetical_queries: ["新问题1", "新问题2"],
        reason: "manual edit HQ",
      });
    });
  });
});
