import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ModelForm from "../ModelForm";
import type { ModelConfig } from "@/api/modelConfig";

// mock API 层 — 断言提交时请求体字段, 不打真实网络
vi.mock("@/api/modelConfig", async (orig) => {
  const actual = await orig<typeof import("@/api/modelConfig")>();
  return {
    ...actual,
    addModelConfig: vi.fn().mockResolvedValue({ id: 1 }),
    updateModelConfig: vi.fn().mockResolvedValue({ id: 1 }),
  };
});
import { addModelConfig, updateModelConfig } from "@/api/modelConfig";

const CHAT_INITIAL: ModelConfig = {
  id: 42, provider: "openai", protocol: "openai", base_url: "http://x",
  api_key: "sk-existing", model_name: "gpt-4o", model_type: "CHAT",
  temperature: 0.0, max_tokens: 12288, max_history_turns: 5, is_active: false,
};
const EMBED_INITIAL: ModelConfig = {
  ...CHAT_INITIAL, id: 43, model_type: "EMBEDDING", model_name: "text-embed",
};

beforeEach(() => vi.clearAllMocks());

describe("ModelForm 历史轮次", () => {
  it("CHAT 编辑态显示历史轮次输入, 回填 initial 值", () => {
    render(<ModelForm open initial={CHAT_INITIAL} onClose={() => {}} onSuccess={() => {}} />);
    expect(screen.getByText("历史轮次")).toBeInTheDocument();
    expect(screen.getByDisplayValue("5")).toBeInTheDocument();
  });

  it("EMBEDDING 态隐藏历史轮次输入", () => {
    render(<ModelForm open initial={EMBED_INITIAL} onClose={() => {}} onSuccess={() => {}} />);
    expect(screen.queryByText("历史轮次")).not.toBeInTheDocument();
  });

  it("L1 契约: 提交时请求体带 max_history_turns(改后的值)", async () => {
    render(<ModelForm open initial={CHAT_INITIAL} onClose={() => {}} onSuccess={() => {}} />);
    const input = screen.getByDisplayValue("5") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "9" } });
    fireEvent.click(screen.getByText("保存修改"));
    await waitFor(() => expect(updateModelConfig).toHaveBeenCalledTimes(1));
    const payload = (updateModelConfig as unknown as ReturnType<typeof vi.fn>).mock.calls[0][0];
    expect(payload.max_history_turns).toBe(9);
  });
});
