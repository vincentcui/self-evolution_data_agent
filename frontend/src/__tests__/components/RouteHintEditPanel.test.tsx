import { render, screen } from "@testing-library/react";
import { fireEvent } from "@testing-library/dom";
import { it, expect, vi } from "vitest";
import RouteHintEditPanel, { RouteHintPayload } from "../../components/audit/RouteHintEditPanel";
import type { CollectionRef } from "../../types";

vi.mock("@/api", () => ({
  getDatabases: vi.fn().mockResolvedValue({ databases: [] }),
  getCollections: vi.fn().mockResolvedValue({ collections: [], db_type: null }),
}));

const baseRefs: CollectionRef[] = [
  { database: "shop", collection: "orders" },
  { database: "shop", collection: "products" },
];

const basePayload: RouteHintPayload = {
  collection_path: baseRefs,
  navigation_note: "items[].sku ↔ products.sku",
};

it("渲染集合路径 Picker 与导航说明", () => {
  render(<RouteHintEditPanel nsId={1} value={basePayload} onChange={() => {}} />);
  // DatabaseCollectionPicker renders two Select components (db + collection)
  // antd Select container + inner input both carry aria-label → use getAllByLabelText
  expect(screen.getAllByLabelText("数据库").length).toBeGreaterThan(0);
  expect(screen.getByLabelText("导航说明")).toHaveValue("items[].sku ↔ products.sku");
});

it("编辑导航说明回调", () => {
  const onChange = vi.fn();
  render(<RouteHintEditPanel nsId={1} value={basePayload} onChange={onChange} />);
  fireEvent.change(screen.getByLabelText("导航说明"), { target: { value: "新说明" } });
  expect(onChange).toHaveBeenCalledWith(
    expect.objectContaining({ navigation_note: "新说明" })
  );
});

it("空集合路径渲染空 Picker", () => {
  render(<RouteHintEditPanel nsId={1} value={{ collection_path: [], navigation_note: "" }} onChange={() => {}} />);
  expect(screen.getAllByLabelText("数据库").length).toBeGreaterThan(0);
  expect(screen.getByLabelText("导航说明")).toHaveValue("");
});
