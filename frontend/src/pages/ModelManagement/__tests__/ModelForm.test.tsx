import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import ModelForm from "../ModelForm";

// Mock API calls
vi.mock("@/api/modelConfig", () => ({
  addModelConfig: vi.fn(),
  updateModelConfig: vi.fn(),
  listModelConfigs: vi.fn(),
  activateModelConfig: vi.fn(),
  deleteModelConfig: vi.fn(),
  testModelConnection: vi.fn(),
  checkModelReady: vi.fn(),
}));

describe("ModelForm namespace 行为", () => {
  it("namespaceId 锁定时显示 namespace 名称", () => {
    render(
      <ModelForm
        open={true}
        initial={null}
        namespaceId={5}
        namespaceName="测试空间"
        onClose={vi.fn()}
        onSuccess={vi.fn()}
      />
    );
    expect(screen.getByText("测试空间")).toBeTruthy();
  });

  it("super_admin 时下拉显示全局选项", () => {
    render(
      <ModelForm
        open={true}
        initial={null}
        isSuperAdmin={true}
        accessibleNamespaces={[{ id: 1, name: "NS-A" }]}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
      />
    );
    // <option value="">全局 → 下拉应有全局选项
    expect(screen.getByText("全局")).toBeTruthy();
  });

  it("锁定模式 + EMBEDDING 时固定显示全局", () => {
    render(
      <ModelForm
        open={true}
        initial={{ model_type: "EMBEDDING", model_name: "e3", provider: "openai", base_url: "", api_key: "", protocol: "openai" } as any}
        onClose={vi.fn()}
        onSuccess={vi.fn()}
      />
    );
    // EMBEDDING 时显示全局文本
    expect(screen.getAllByText("全局").length).toBeGreaterThan(0);
  });
});
