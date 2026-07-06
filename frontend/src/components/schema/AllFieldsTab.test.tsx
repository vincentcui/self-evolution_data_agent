import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { AllFieldsTab } from "./AllFieldsTab";
import type { SchemaCanonicalField } from "@/types/schema-canonical";

const FAKE_FIELDS: SchemaCanonicalField[] = [
  {
    name: "id",
    type: "bigint",
    description: "主键",
    description_confidence: "confirmed_by_introspect",
    enum_values: [],
    user_locked: false,
  },
  {
    name: "status",
    type: "int",
    description: "状态",
    description_confidence: "confirmed_by_code",
    enum_values: [{ name: "PAID", db_value: 2, description: "已支付" }],
    user_locked: false,
  },
];

const FAKE_SCO = {
  id: 1,
  target: "t_order",
  fields: FAKE_FIELDS,
  user_locked: false,
  description: "测试表",
  purpose_detail: "",
  relationships: [],
};

describe("AllFieldsTab", () => {
  it("renders fields with confidence tags", () => {
    render(<AllFieldsTab sco={FAKE_SCO} namespaceId={1} onOpenEvidence={vi.fn()} onOpenHistory={vi.fn()} onLockField={vi.fn()} />);
    expect(screen.getByText("DBA 注释")).toBeInTheDocument();
    expect(screen.getByText("代码确认")).toBeInTheDocument();
  });

  it("renders 3 action icons per field", () => {
    render(<AllFieldsTab sco={FAKE_SCO} namespaceId={1} onOpenEvidence={vi.fn()} onOpenHistory={vi.fn()} onLockField={vi.fn()} />);
    expect(screen.getAllByLabelText("证据")).toHaveLength(2);
    expect(screen.getAllByLabelText("历史")).toHaveLength(2);
    expect(screen.getAllByLabelText("锁定")).toHaveLength(2);
  });

  it("calls onOpenEvidence with field name when 证据 clicked", () => {
    const onOpenEvidence = vi.fn();
    render(<AllFieldsTab sco={FAKE_SCO} namespaceId={1} onOpenEvidence={onOpenEvidence} onOpenHistory={vi.fn()} onLockField={vi.fn()} />);
    const buttons = screen.getAllByLabelText("证据");
    fireEvent.click(buttons[1]);
    expect(onOpenEvidence).toHaveBeenCalledWith("status");
  });

  it("renders enum_values inline as count badge", () => {
    render(<AllFieldsTab sco={FAKE_SCO} namespaceId={1} onOpenEvidence={vi.fn()} onOpenHistory={vi.fn()} onLockField={vi.fn()} />);
    expect(screen.getByText("枚举: 1")).toBeInTheDocument();
  });
});
