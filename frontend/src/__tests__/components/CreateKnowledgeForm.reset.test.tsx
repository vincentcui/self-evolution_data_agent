/* ════════════════════════════════════════════════════════════════════════════
 *  CreateKnowledgeForm — 弹窗重置行为测试 (ZYZ-55)
 *  验证：关闭后再次打开，所有字段回到初始空值，不带出上次填写的历史数据。
 * ══════════════════════════════════════════════════════════════════════════ */

import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import CreateKnowledgeForm from "@/components/audit/CreateKnowledgeForm";

// mock 结构与现有 CreateKnowledgeForm.test.tsx 保持一致
vi.mock("@/api", () => ({
  createKnowledge:  vi.fn().mockResolvedValue({ id: 1 }),
  getDatabases:     vi.fn().mockResolvedValue({ databases: [] }),
  getCollections:   vi.fn().mockResolvedValue({ collections: [], db_type: null }),
}));

beforeEach(() => vi.clearAllMocks());

// Ant Design 部分组件依赖 jsdom 未实现的 API
Object.defineProperty(window, "scrollTo", { value: vi.fn(), writable: true });

const noop = () => {};

describe("CreateKnowledgeForm — 弹窗重置 (ZYZ-55)", () => {
  it("关闭弹窗后重新打开，entryType 恢复默认 terminology", async () => {
    const user = userEvent.setup();
    const { fireEvent } = await import("@testing-library/dom");

    const { rerender } = render(
      <CreateKnowledgeForm
        open={true}
        defaultNamespaceId={1}
        onClose={noop}
        onSubmitted={noop}
      />
    );

    // antd Select 用 mouseDown 打开，第一个 .ant-select-selector = 类型 Select
    const typeSelectors = document.querySelectorAll(".ant-select-selector");
    fireEvent.mouseDown(typeSelectors[0]);
    const ruleOption = await screen.findByText(/查询规则.*查询约束/);
    await user.click(ruleOption);

    // 关闭弹窗
    await act(async () => {
      rerender(
        <CreateKnowledgeForm
          open={false}
          defaultNamespaceId={1}
          onClose={noop}
          onSubmitted={noop}
        />
      );
    });

    // 重新打开
    await act(async () => {
      rerender(
        <CreateKnowledgeForm
          open={true}
          defaultNamespaceId={1}
          onClose={noop}
          onSubmitted={noop}
        />
      );
    });

    // entryType 回到默认 terminology — TerminologyEditPanel 的"术语"字段应出现
    expect(await screen.findByLabelText("术语")).toBeInTheDocument();
  });

  it("关闭弹窗后重新打开，rule_text 字段恢复为空", async () => {
    const user = userEvent.setup();
    const { fireEvent } = await import("@testing-library/dom");

    const { rerender } = render(
      <CreateKnowledgeForm
        open={true}
        defaultNamespaceId={1}
        onClose={noop}
        onSubmitted={noop}
      />
    );

    // 切到 rule 类型
    const typeSelectors = document.querySelectorAll(".ant-select-selector");
    fireEvent.mouseDown(typeSelectors[0]);
    const ruleOption = await screen.findByText(/查询规则.*查询约束/);
    await user.click(ruleOption);

    // 填写 rule_text
    const ruleTextArea = screen.getByLabelText("规则文本");
    await user.type(ruleTextArea, "这是一条测试规则，不应在下次打开时保留");
    expect(ruleTextArea).toHaveValue("这是一条测试规则，不应在下次打开时保留");

    // 关闭 → 重新打开
    await act(async () => {
      rerender(
        <CreateKnowledgeForm
          open={false}
          defaultNamespaceId={1}
          onClose={noop}
          onSubmitted={noop}
        />
      );
    });
    await act(async () => {
      rerender(
        <CreateKnowledgeForm
          open={true}
          defaultNamespaceId={1}
          onClose={noop}
          onSubmitted={noop}
        />
      );
    });

    // 重置后默认回到 terminology 类型，切回 rule 检查 rule_text 是否清空
    const typeSelectors2 = document.querySelectorAll(".ant-select-selector");
    fireEvent.mouseDown(typeSelectors2[0]);
    const ruleOption2 = await screen.findByText(/查询规则.*查询约束/);
    await user.click(ruleOption2);

    // rule_text 应为空
    const ruleTextArea2 = screen.getByLabelText("规则文本");
    expect(ruleTextArea2).toHaveValue("");
  });
});
