/* ════════════════════════════════════════════════════════════════════════════
 *  CreateKnowledgeForm — 按 entry_type 自适应的添加知识 Modal
 * ══════════════════════════════════════════════════════════════════════════ */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import CreateKnowledgeForm from "@/components/audit/CreateKnowledgeForm";

vi.mock("@/api", () => ({
  createKnowledge: vi.fn(),
  getDatabases: vi.fn().mockResolvedValue({ databases: [] }),
  getCollections: vi.fn().mockResolvedValue({ collections: [], db_type: null }),
}));

beforeEach(() => vi.clearAllMocks());

describe("CreateKnowledgeForm", () => {
  it("默认 terminology — 渲染 TerminologyEditPanel 字段", async () => {
    render(
      <CreateKnowledgeForm
        open
        defaultNamespaceId={1}
        onClose={() => {}}
        onSubmitted={() => {}}
      />,
    );
    expect(await screen.findByLabelText("术语")).toBeInTheDocument();
    // antd Select 容器与内部 input 同时承载 aria-label, 故用 getAllByLabelText 断言存在.
    expect(screen.getAllByLabelText("数据库").length).toBeGreaterThan(0);
    expect(screen.getByLabelText("数据库类型")).toBeInTheDocument();
    expect(screen.getAllByLabelText("集合/表").length).toBeGreaterThan(0);
    expect(screen.getAllByLabelText("同义词").length).toBeGreaterThan(0);
  });

  it("terminology 缺 db_type 不能提交 — 不调 createKnowledge", async () => {
    const { createKnowledge } = await import("@/api");
    const user = userEvent.setup();
    render(
      <CreateKnowledgeForm
        open
        defaultNamespaceId={1}
        onClose={() => {}}
        onSubmitted={() => {}}
      />,
    );
    const term = await screen.findByLabelText("术语");
    await user.type(term, "GMV");
    await user.click(screen.getByRole("button", { name: /确定|OK/ }));
    expect(createKnowledge).not.toHaveBeenCalled();
  });

  it("rule 切换 → rule_text 必填; 填后提交 createKnowledge body 含 payload.rule_text", async () => {
    const { createKnowledge } = await import("@/api");
    (createKnowledge as any).mockResolvedValue({ entry: { id: 99 }, conflicts: [], overflow: false });
    const onSubmitted = vi.fn();
    const user = userEvent.setup();
    render(
      <CreateKnowledgeForm
        open
        defaultNamespaceId={1}
        onClose={() => {}}
        onSubmitted={onSubmitted}
      />,
    );

    // 切到 rule 类型 — antd Select 用 mouseDown 打开
    const { fireEvent } = await import("@testing-library/dom");
    const typeSelectors = document.querySelectorAll(".ant-select-selector");
    // 第一个 Select = "类型" (Form 顺序: 类型 / 生效范围 / 优先级 / [type-specific])
    fireEvent.mouseDown(typeSelectors[0]);
    const ruleOption = await screen.findByText(/查询规则.*查询约束/);
    await user.click(ruleOption);

    const ruleText = await screen.findByLabelText("规则文本");
    await user.type(ruleText, "查订单按下单时间倒序");

    await user.click(screen.getByRole("button", { name: /确定|OK/ }));

    await waitFor(() => expect(createKnowledge).toHaveBeenCalledTimes(1));
    const body = (createKnowledge as any).mock.calls[0][0];
    expect(body.entry_type).toBe("rule");
    expect(body.payload.rule_text).toBe("查订单按下单时间倒序");
    expect(body.content).toBe("查订单按下单时间倒序");
    expect(onSubmitted).toHaveBeenCalled();
  });

  it("example 非法 JSON → 阻止提交并展示 jsonError", async () => {
    const { createKnowledge } = await import("@/api");
    const user = userEvent.setup();
    render(
      <CreateKnowledgeForm
        open
        defaultNamespaceId={1}
        onClose={() => {}}
        onSubmitted={() => {}}
      />,
    );

    const { fireEvent } = await import("@testing-library/dom");
    const typeSelectors = document.querySelectorAll(".ant-select-selector");
    fireEvent.mouseDown(typeSelectors[0]);
    const opt = await screen.findByText(/示例查询.*成功查询案例/);
    await user.click(opt);

    await user.type(await screen.findByLabelText("问题模式"), "Q1");
    await user.type(screen.getByLabelText("涉及集合"), "shop.orders");
    // userEvent.type 把 `{` 解析为修饰符, 需 `{{` 转义.
    await user.type(screen.getByLabelText("查询计划"), "{{ this is not json");

    await user.click(screen.getByRole("button", { name: /确定|OK/ }));

    expect(createKnowledge).not.toHaveBeenCalled();
    expect(await screen.findByText(/格式不合法/)).toBeInTheDocument();
  });
});
