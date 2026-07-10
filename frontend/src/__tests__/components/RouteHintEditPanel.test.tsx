import { render, screen, fireEvent } from "@testing-library/react";
import { it, expect, vi } from "vitest";
import RouteHintEditPanel, { RouteHintPayload } from "../../components/audit/RouteHintEditPanel";

const basePayload: RouteHintPayload = {
  collection_path: ["shop.orders", "shop.products"],
  navigation_note: "items[].sku ↔ products.sku",
};

it("渲染集合路径与导航说明", () => {
  render(<RouteHintEditPanel value={basePayload} onChange={() => {}} />);
  expect(screen.getByLabelText("集合路径")).toHaveValue("shop.orders,shop.products");
  expect(screen.getByLabelText("导航说明")).toHaveValue("items[].sku ↔ products.sku");
});

it("编辑导航说明回调", () => {
  const onChange = vi.fn();
  render(<RouteHintEditPanel value={basePayload} onChange={onChange} />);
  fireEvent.change(screen.getByLabelText("导航说明"), { target: { value: "新说明" } });
  expect(onChange).toHaveBeenCalledWith(
    expect.objectContaining({ navigation_note: "新说明" })
  );
});

it("空集合路径渲染空串", () => {
  render(<RouteHintEditPanel value={{ collection_path: [], navigation_note: "" }} onChange={() => {}} />);
  expect(screen.getByLabelText("集合路径")).toHaveValue("");
});
