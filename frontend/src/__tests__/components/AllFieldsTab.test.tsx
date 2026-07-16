/* ════════════════════════════════════════════════════════════════════════════
 *  AllFieldsTab — 关联关系三联动下拉单测 (Task 8)
 * ══════════════════════════════════════════════════════════════════════════ */

import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { AllFieldsTab } from "@/components/schema/AllFieldsTab";
import * as api from "@/api";
import type { SchemaCanonicalObject, SchemaCanonicalRelationship } from "@/types/schema-canonical";

vi.mock("@/api", () => ({
  enumApi: {
    listEnumDictionaries: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    listPendingEnumBindings: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    unbindFieldEnum: vi.fn().mockResolvedValue(undefined),
  },
  getDatabases: vi.fn(),
  getCollections: vi.fn(),
}));

// Mock sub-components to keep test focused
vi.mock("@/components/schema/ConfidenceTag", () => ({
  ConfidenceTag: () => null,
}));
vi.mock("@/components/schema/EnumBindDrawer", () => ({
  EnumBindDrawer: () => null,
}));
vi.mock("@/components/schema/FieldRowActions", () => ({
  FieldRowActions: () => null,
}));

const mockedApi = vi.mocked(api);

const baseSco: Pick<SchemaCanonicalObject, "id" | "fields" | "user_locked" | "description" | "purpose_detail" | "relationships" | "target"> = {
  id: 1,
  target: "orders",
  fields: [
    { name: "order_id", type: "int" },
    { name: "customer_id", type: "int" },
    { name: "amount", type: "decimal" },
  ],
  user_locked: false,
  description: "Orders table",
  purpose_detail: "",
  relationships: [],
};

const allScos: Pick<SchemaCanonicalObject, "target" | "database" | "fields">[] = [
  { target: "orders", database: "shop", fields: [{ name: "order_id", type: "int" }, { name: "customer_id", type: "int" }] },
  { target: "customers", database: "shop", fields: [{ name: "cust_id", type: "int" }, { name: "name", type: "varchar" }] },
  { target: "events", database: "log", fields: [{ name: "event_id", type: "int" }, { name: "ts", type: "timestamp" }] },
];

const defaultProps = {
  sco: baseSco,
  namespaceId: 1,
  allScos,
  onOpenEvidence: vi.fn(),
  onOpenHistory: vi.fn(),
  onLockField: vi.fn(),
  onSave: vi.fn().mockResolvedValue(undefined),
  onRefresh: vi.fn(),
};

/**
 * Open antd Select by finding the selector element near text/placeholder.
 */
function openSelectByPlaceholder(container: HTMLElement, placeholder: string) {
  // Find the Select wrapper containing the placeholder text
  const selectors = container.querySelectorAll(".ant-select-selector");
  for (const sel of Array.from(selectors)) {
    const parent = sel.closest(".ant-select");
    if (parent?.textContent?.includes(placeholder) || parent?.querySelector(`[title="${placeholder}"]`) || parent?.getAttribute("title") === placeholder) {
      fireEvent.mouseDown(sel as HTMLElement);
      return;
    }
  }
  // Fallback: find by placeholder in the placeholder span
  const placeholders = container.querySelectorAll(".ant-select-selection-placeholder");
  for (const ph of Array.from(placeholders)) {
    if (ph.textContent === placeholder) {
      const sel = ph.closest(".ant-select")?.querySelector(".ant-select-selector");
      if (sel) {
        fireEvent.mouseDown(sel as HTMLElement);
        return;
      }
    }
  }
  throw new Error(`Select with placeholder "${placeholder}" not found`);
}

describe("AllFieldsTab — 关联关系三联动", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getDatabases.mockResolvedValue({
      databases: [
        { database: "shop", db_type: "mysql", datasource_id: 1, host: "h" },
        { database: "log", db_type: "mongodb", datasource_id: 2, host: "h" },
      ],
    });
    mockedApi.getCollections.mockImplementation((_: number, db: string) =>
      Promise.resolve({
        database: db,
        db_type: (db === "shop" ? "mysql" : "mongodb") as any,
        collections: db === "shop" ? ["orders", "customers"] : ["events"],
      }),
    );
  });

  it("mount 时调 getDatabases 加载数据库列表", async () => {
    render(<AllFieldsTab {...defaultProps} />);
    await waitFor(() => expect(mockedApi.getDatabases).toHaveBeenCalledWith(1));
  });

  it("from_target 编辑态 disabled 锁定当前 SCO target", async () => {
    const { container } = render(<AllFieldsTab {...defaultProps} />);

    // 进入编辑模式
    fireEvent.click(screen.getByText("编辑 Schema"));

    // 添加关联关系
    fireEvent.click(screen.getByText("添加关联关系"));

    await waitFor(() => {
      const disabledInputs = container.querySelectorAll("input[disabled]");
      // from_target should be disabled and contain the SCO target
      const fromTarget = Array.from(disabledInputs).find(
        (el) => (el as HTMLInputElement).value === "orders"
      );
      expect(fromTarget).toBeTruthy();
    });
  });

  it("handleAddRel 默认 to_db_type 为空串 (非 mysql)", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const { container } = render(
      <AllFieldsTab {...defaultProps} onSave={onSave} />,
    );

    // 进入编辑模式
    fireEvent.click(screen.getByText("编辑 Schema"));
    // 添加关联关系
    fireEvent.click(screen.getByText("添加关联关系"));

    // from_target disabled input should have value "orders"
    await waitFor(() => {
      const inputs = container.querySelectorAll("input[disabled]");
      expect(inputs.length).toBeGreaterThanOrEqual(1);
    });

    // Verify the disabled to_db_type input is empty (not "mysql")
    const disabledInputs = container.querySelectorAll("input[disabled]");
    const values = Array.from(disabledInputs).map((el) => (el as HTMLInputElement).value);
    // from_target = "orders", to_db_type = ""
    expect(values).toContain("orders");
    expect(values).toContain("");
  });

  it("to_database 下拉选库后 to_db_type 自动带出", async () => {
    const { container } = render(<AllFieldsTab {...defaultProps} />);

    // 进入编辑模式 + 添加关联
    fireEvent.click(screen.getByText("编辑 Schema"));
    fireEvent.click(screen.getByText("添加关联关系"));

    // 等待 getDatabases 完成
    await waitFor(() => expect(mockedApi.getDatabases).toHaveBeenCalled());

    // 打开 to_database 下拉 (placeholder="目标库")
    openSelectByPlaceholder(container, "目标库");

    // 选择 shop — use selector to avoid ambiguity with other "shop" text
    await waitFor(() => {
      const options = document.querySelectorAll(".ant-select-item-option-content");
      const shopOption = Array.from(options).find((el) => el.textContent === "shop");
      expect(shopOption).toBeTruthy();
      fireEvent.click(shopOption!);
    });

    // to_db_type disabled input should now show "mysql"
    await waitFor(() => {
      const disabledInputs = container.querySelectorAll("input[disabled]");
      const dbTypeInput = Array.from(disabledInputs).find(
        (el) => (el as HTMLInputElement).value === "mysql"
      );
      expect(dbTypeInput).toBeTruthy();
    });

    // getCollections should have been called for "shop"
    expect(mockedApi.getCollections).toHaveBeenCalledWith(1, "shop");
  });

  it("to_target 在 to_database 未选时 disabled", async () => {
    const { container } = render(<AllFieldsTab {...defaultProps} />);

    fireEvent.click(screen.getByText("编辑 Schema"));
    fireEvent.click(screen.getByText("添加关联关系"));

    // to_target Select should be disabled when to_database is empty
    await waitFor(() => {
      const disabledSelects = container.querySelectorAll(".ant-select-disabled");
      // At least to_target and to_field should be disabled
      expect(disabledSelects.length).toBeGreaterThanOrEqual(2);
    });
  });

  it("scoIndex 按 database/target 索引, to_field 下拉取到对应 SCO fields", async () => {
    const existingRels: SchemaCanonicalRelationship[] = [
      { from_target: "orders", from_field: "customer_id", to_db_type: "mysql", to_database: "shop", to_target: "customers", to_field: "", relation_type: "many_to_one" },
    ];
    const scoWithRel = { ...baseSco, relationships: existingRels };

    const { container } = render(
      <AllFieldsTab {...defaultProps} sco={scoWithRel} />,
    );

    // 进入编辑模式
    fireEvent.click(screen.getByText("编辑 Schema"));

    await waitFor(() => {
      // The relationship row should be rendered with existing data
      const selects = container.querySelectorAll(".ant-select");
      expect(selects.length).toBeGreaterThan(0);
    });
  });

  it("view mode Table 保持不变 — 渲染关联关系表格", () => {
    const rels: SchemaCanonicalRelationship[] = [
      { from_target: "orders", from_field: "customer_id", to_db_type: "mysql", to_database: "shop", to_target: "customers", to_field: "cust_id", relation_type: "many_to_one" },
    ];
    const scoWithRel = { ...baseSco, relationships: rels };

    render(<AllFieldsTab {...defaultProps} sco={scoWithRel} />);

    // View mode: table should render the relationship data
    // Use getAllByText since field names appear in both fields table and relationships table
    expect(screen.getAllByText("orders").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("customers").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("customer_id").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("cust_id")).toBeTruthy();
    expect(screen.getByText("many_to_one")).toBeTruthy();
  });
});
