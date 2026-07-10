import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import ModelForm from "../ModelForm";

vi.mock("@/api/modelConfig", () => ({
  addModelConfig: vi.fn(), updateModelConfig: vi.fn(), listModelConfigs: vi.fn(),
  activateModelConfig: vi.fn(), deleteModelConfig: vi.fn(), testModelConnection: vi.fn(),
  checkModelReady: vi.fn(),
}));

const countTypeRadios = (c: HTMLElement) =>
  c.querySelectorAll('input[name="mf_type"]').length;

describe("ModelForm EMBEDDING 门槛 (D2 UX)", () => {
  it("非 super_admin 新增 → 只有 CHAT 类型 (隐藏 EMBEDDING)", () => {
    const { container } = render(
      <ModelForm open initial={null} isSuperAdmin={false}
        accessibleNamespaces={[]} onClose={vi.fn()} onSuccess={vi.fn()} />
    );
    expect(countTypeRadios(container)).toBe(1);
  });

  it("super_admin 新增 → CHAT + EMBEDDING 两种", () => {
    const { container } = render(
      <ModelForm open initial={null} isSuperAdmin={true}
        accessibleNamespaces={[]} onClose={vi.fn()} onSuccess={vi.fn()} />
    );
    expect(countTypeRadios(container)).toBe(2);
  });
});
