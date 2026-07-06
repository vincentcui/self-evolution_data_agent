/* ════════════════════════════════════════════════════════════════════════════
 *  CreateKnowledgeForm — 弹窗重置行为测试 (ZYZ-55)
 *  验证：关闭后再次打开，所有字段回到初始空值，不带出上次填写的历史数据。
 * ══════════════════════════════════════════════════════════════════════════ */

import { render, screen, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import CreateKnowledgeForm from "@/components/audit/CreateKnowledgeForm";

vi.mock("@/api", () => ({
  createKnowledge: vi.fn().mockResolvedValue({ id: 1 }),
  // TerminologyEditPanel mount 时调用，不补会导致 "No export" 报错
  getDatabases:    vi.fn().mockResolvedValue([]),
  getCollections:  vi.fn().mockResolvedValue([]),
}));

// Ant Design 部分组件依赖 getComputedStyle / scrollTo 等 jsdom 未实现的 API
Object.defineProperty(window, "scrollTo", { value: vi.fn(), writable: true });

const noop = () => {};

describe("CreateKnowledgeForm — 弹窗重置 (ZYZ-55)", () => {
  it("关闭弹窗后重新打开，entryType 恢复默认 terminology", async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <CreateKnowledgeForm
        open={true}
        defaultNamespaceId={1}
        onClose={noop}
        onSubmitted={noop}
      />
    );

    // 切换类型到 "rule"
    const typeSelect = screen.getByLabelText("类型");
    await user.click(typeSelect);
    const ruleOption = await screen.findByText("查询规则 (rule)");
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

    // entryType 应回到默认 "terminology"（Select 显示"业务术语"）
    const select = screen.getByLabelText("类型");
    expect(select).toHaveTextContent("业务术语 (terminology)");
  });

  it("关闭弹窗后重新打开，rule_text 字段恢复为空", async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <CreateKnowledgeForm
        open={true}
        defaultNamespaceId={1}
        onClose={noop}
        onSubmitted={noop}
      />
    );

    // 切换到 rule 类型并填写内容
    const typeSelect = screen.getByLabelText("类型");
    await user.click(typeSelect);
    const ruleOption = await screen.findByText("查询规则 (rule)");
    await user.click(ruleOption);

    const ruleTextArea = screen.getByLabelText("rule_text");
    await user.type(ruleTextArea, "这是一条测试查询规则，不应在下次打开时保留");
    expect(ruleTextArea).toHaveValue("这是一条测试查询规则，不应在下次打开时保留");

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

    // 回到 rule 类型重新检查（先切回 rule）
    const typeSelect2 = screen.getByLabelText("类型");
    await user.click(typeSelect2);
    const ruleOption2 = await screen.findByText("查询规则 (rule)");
    await user.click(ruleOption2);

    // rule_text 应为空
    const ruleTextArea2 = screen.getByLabelText("rule_text");
    expect(ruleTextArea2).toHaveValue("");
  });
});
