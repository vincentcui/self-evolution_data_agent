/* ════════════════════════════════════════════════════════════════════════════
 *  DatabaseCollectionPicker — 隔离单测 (vitest + @testing-library/react)
 * ══════════════════════════════════════════════════════════════════════════ */

import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { DatabaseCollectionPicker } from "@/components/audit/DatabaseCollectionPicker";
import * as api from "@/api";
import type { CollectionRef } from "@/types";

vi.mock("@/api", () => ({
  getDatabases: vi.fn(),
  getCollections: vi.fn(),
}));

const mockedApi = vi.mocked(api);

/**
 * 点开 antd Select — 通过 aria-label 找到 .ant-select 容器,
 * 再取 .ant-select-selector 触发 mouseDown (antd v5 标准做法).
 */
function openSelect(container: HTMLElement, ariaLabel: string) {
  const wrapper = container.querySelector(`[aria-label="${ariaLabel}"]`) as HTMLElement;
  if (!wrapper) throw new Error(`Select with aria-label="${ariaLabel}" not found`);
  const selector = wrapper.querySelector(".ant-select-selector") as HTMLElement;
  if (!selector) throw new Error(`.ant-select-selector not found inside [aria-label="${ariaLabel}"]`);
  fireEvent.mouseDown(selector);
}

describe("DatabaseCollectionPicker", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedApi.getDatabases.mockResolvedValue({ databases: [
      { database: "shop", db_type: "mysql", datasource_id: 1, host: "h" },
      { database: "log", db_type: "mongodb", datasource_id: 2, host: "h" },
    ]});
    mockedApi.getCollections.mockImplementation((_: number, db: string) =>
      Promise.resolve({
        database: db,
        db_type: (db === "shop" ? "mysql" : "mongodb") as "mysql" | "mongodb",
        collections: db === "shop" ? ["orders", "products"] : ["events"],
      }),
    );
  });

  it("multiple 模式跨库多选按顺序保留", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const { container } = render(
      <DatabaseCollectionPicker nsId={1} mode="multiple" value={[]} onChange={onChange} />,
    );

    // 等待 getDatabases 响应
    await waitFor(() => expect(mockedApi.getDatabases).toHaveBeenCalledWith(1));

    // 选择 shop 数据库
    openSelect(container, "数据库");
    await user.click(await screen.findByText("shop (mysql)"));

    // 等待 collections 加载 (mysql → isSql → aria-label="表")
    await waitFor(() => expect(mockedApi.getCollections).toHaveBeenCalledWith(1, "shop"));

    // 打开集合下拉
    openSelect(container, "表");
    await user.click(await screen.findByText("orders"));

    expect(onChange).toHaveBeenLastCalledWith([{ database: "shop", collection: "orders" }]);
  });

  it("single 模式产出单个 CollectionRef", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const { container } = render(
      <DatabaseCollectionPicker nsId={1} mode="single" value={[]} onChange={onChange} />,
    );

    await waitFor(() => expect(mockedApi.getDatabases).toHaveBeenCalledWith(1));

    openSelect(container, "数据库");
    await user.click(await screen.findByText("shop (mysql)"));

    await waitFor(() => expect(mockedApi.getCollections).toHaveBeenCalledWith(1, "shop"));

    openSelect(container, "表");
    await user.click(await screen.findByText("orders"));

    expect(onChange).toHaveBeenLastCalledWith([{ database: "shop", collection: "orders" }]);
  });

  it("multiple 模式: 选 A 库切到 B 库后 A 的已选仍保留在 value 中", async () => {
    const user = userEvent.setup();
    const value: CollectionRef[] = [{ database: "shop", collection: "orders" }];
    const onChange = vi.fn();
    const { container } = render(
      <DatabaseCollectionPicker nsId={1} mode="multiple" value={value} onChange={onChange} />,
    );

    await waitFor(() => expect(mockedApi.getDatabases).toHaveBeenCalledWith(1));

    // 切到 log 库 (mongodb → aria-label="集合")
    openSelect(container, "数据库");
    await user.click(await screen.findByText("log (mongodb)"));

    await waitFor(() => expect(mockedApi.getCollections).toHaveBeenCalledWith(1, "log"));

    // 打开集合下拉, 选 events
    openSelect(container, "集合");
    await user.click(await screen.findByText("events"));

    // onChange 应同时包含 shop.orders (跨库保留) + log.events (新选)
    expect(onChange).toHaveBeenLastCalledWith([
      { database: "shop", collection: "orders" },
      { database: "log", collection: "events" },
    ]);
  });

  it("single 模式: 切库时 onChange([]) 被调用以重置", async () => {
    const user = userEvent.setup();
    const value: CollectionRef[] = [{ database: "shop", collection: "orders" }];
    const onChange = vi.fn();
    const { container } = render(
      <DatabaseCollectionPicker nsId={1} mode="single" value={value} onChange={onChange} />,
    );

    await waitFor(() => expect(mockedApi.getDatabases).toHaveBeenCalledWith(1));

    // 切到 log 库
    openSelect(container, "数据库");
    await user.click(await screen.findByText("log (mongodb)"));

    // single 模式切库 → onChange([]) 重置
    expect(onChange).toHaveBeenCalledWith([]);
  });

  it("tagRender 文本包含 'shop.orders' 格式", async () => {
    const value: CollectionRef[] = [{ database: "shop", collection: "orders" }];
    const onChange = vi.fn();
    const { container } = render(
      <DatabaseCollectionPicker nsId={1} mode="multiple" value={value} onChange={onChange} />,
    );

    await waitFor(() => expect(mockedApi.getDatabases).toHaveBeenCalledWith(1));

    // shop 是当前 db, orders 在 options 中, tag 应该显示 shop.orders
    openSelect(container, "数据库");
    const shopOption = await screen.findByText("shop (mysql)");
    fireEvent.click(shopOption);

    await waitFor(() => expect(mockedApi.getCollections).toHaveBeenCalledWith(1, "shop"));

    // 打开 collection select 触发渲染 tags
    openSelect(container, "表");

    // tagRender 应该渲染 "shop.orders" 格式文本
    await waitFor(() => {
      expect(container.textContent).toContain("shop.orders");
    });
  });
});
