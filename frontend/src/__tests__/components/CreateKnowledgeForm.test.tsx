/* ════════════════════════════════════════════════════════════════════════════
 *  CreateKnowledgeForm — 按 entry_type 自适应的添加知识 Modal
 * ══════════════════════════════════════════════════════════════════════════ */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi, beforeEach } from "vitest";
import CreateKnowledgeForm from "@/components/audit/CreateKnowledgeForm";

vi.mock("@/api", () => ({
  createKnowledge: vi.fn(),
  getDatabases: vi.fn().mockResolvedValue({
    databases: [
      { database: "shop_db", db_type: "mongodb" },
    ],
  }),
  getCollections: vi.fn().mockResolvedValue({
    database: "shop_db", db_type: "mongodb",
    collections: ["orders", "products"],
  }),
}));

beforeEach(() => vi.clearAllMocks());

describe("CreateKnowledgeForm", () => {
  it("默认 terminology — 渲染 TerminologyEditPanel 字段", async () => {
    render(
      <CreateKnowledgeForm
        open
        defaultNamespaceId={1}
        onClose={() => {}}
        onSubmitted={() => {}}
      />,
    );
    expect(await screen.findByLabelText("术语")).toBeInTheDocument();
    // antd Select 容器与内部 input 同时承载 aria-label, 故用 getAllByLabelText 断言存在.
    expect(screen.getAllByLabelText("数据库").length).toBeGreaterThan(0);
    expect(screen.getByLabelText("数据库类型")).toBeInTheDocument();
    expect(screen.getAllByLabelText("集合/表").length).toBeGreaterThan(0);
    expect(screen.getAllByLabelText("同义词").length).toBeGreaterThan(0);
  });

  it("terminology 缺 db_type 不能提交 — 不调 createKnowledge", async () => {
    const { createKnowledge } = await import("@/api");
    const user = userEvent.setup();
    render(
      <CreateKnowledgeForm
        open
        defaultNamespaceId={1}
        onClose={() => {}}
        onSubmitted={() => {}}
      />,
    );
    const term = await screen.findByLabelText("术语");
    await user.type(term, "GMV");
    await user.click(screen.getByRole("button", { name: /确定|OK/ }));
    expect(createKnowledge).not.toHaveBeenCalled();
  });

  it("rule 切换 → rule_text 必填; 填后提交 createKnowledge body 含 payload.rule_text", async () => {
    const { createKnowledge } = await import("@/api");
    (createKnowledge as any).mockResolvedValue({ entry: { id: 99 }, conflicts: [], overflow: false });
    const onSubmitted = vi.fn();
    const user = userEvent.setup();
    render(
      <CreateKnowledgeForm
        open
        defaultNamespaceId={1}
        onClose={() => {}}
        onSubmitted={onSubmitted}
      />,
    );

    // 切到 rule 类型 — antd Select 用 mouseDown 打开
    const { fireEvent } = await import("@testing-library/dom");
    const typeSelectors = document.querySelectorAll(".ant-select-selector");
    // 第一个 Select = "类型" (Form 顺序: 类型 / 生效范围 / 优先级 / [type-specific])
    fireEvent.mouseDown(typeSelectors[0]);
    const ruleOption = await screen.findByText(/查询规则.*查询约束/);
    await user.click(ruleOption);

    const ruleText = await screen.findByLabelText("规则文本");
    await user.type(ruleText, "查订单按下单时间倒序");

    await user.click(screen.getByRole("button", { name: /确定|OK/ }));

    await waitFor(() => expect(createKnowledge).toHaveBeenCalledTimes(1));
    const body = (createKnowledge as any).mock.calls[0][0];
    expect(body.entry_type).toBe("rule");
    expect(body.payload.rule_text).toBe("查订单按下单时间倒序");
    expect(body.content).toBe("查订单按下单时间倒序");
    expect(onSubmitted).toHaveBeenCalled();
  });

  it("route_hint 切换 → 渲染 DatabaseCollectionPicker, 提交 payload.collection_path 为 CollectionRef[]", async () => {
    const { createKnowledge } = await import("@/api");
    (createKnowledge as any).mockResolvedValue({ entry: { id: 100 }, conflicts: [], overflow: false });
    const onSubmitted = vi.fn();
    const user = userEvent.setup();
    const { fireEvent } = await import("@testing-library/dom");
    render(
      <CreateKnowledgeForm
        open
        defaultNamespaceId={1}
        onClose={() => {}}
        onSubmitted={onSubmitted}
      />,
    );

    // 切到 route_hint 类型
    const typeSelectors = document.querySelectorAll(".ant-select-selector");
    fireEvent.mouseDown(typeSelectors[0]);
    const rhOption = await screen.findByText(/路由偏好.*多表关联路径/);
    await user.click(rhOption);

    // DatabaseCollectionPicker 渲染: 数据库 Select + 集合 Select
    const dbInputs = screen.getAllByLabelText("数据库");
    expect(dbInputs.length).toBeGreaterThan(0);

    // 用 aria-label 定位 database Select 的 selector
    const dbSelectEl = dbInputs[0].closest(".ant-select")?.querySelector(".ant-select-selector");
    expect(dbSelectEl).toBeTruthy();
    fireEvent.mouseDown(dbSelectEl!);
    const dbOption = await screen.findByText(/shop_db \(mongodb\)/);
    await user.click(dbOption);

    // 等待 collections 加载
    await new Promise((r) => setTimeout(r, 200));

    // 选择 collection
    const collInputs = screen.getAllByLabelText(/集合|表/);
    const collSelectEl = collInputs[0].closest(".ant-select")?.querySelector(".ant-select-selector");
    expect(collSelectEl).toBeTruthy();
    fireEvent.mouseDown(collSelectEl!);
    const collOption = await screen.findByText("orders");
    await user.click(collOption);

    // 填写问题模式
    await user.type(screen.getByLabelText("问题模式"), "查 X 关联的 Y");

    // 提交
    await user.click(screen.getByRole("button", { name: /确定|OK/ }));

    await waitFor(() => expect(createKnowledge).toHaveBeenCalledTimes(1));
    const body = (createKnowledge as any).mock.calls[0][0];
    expect(body.entry_type).toBe("route_hint");
    expect(body.content).toBe("查 X 关联的 Y");
    expect(body.payload.collection_path).toEqual([{ database: "shop_db", collection: "orders" }]);
    expect(onSubmitted).toHaveBeenCalled();
  });

  it("example 非法 JSON → 阻止提交并展示 jsonError", async () => {
    const { createKnowledge } = await import("@/api");
    const user = userEvent.setup();
    render(
      <CreateKnowledgeForm
        open
        defaultNamespaceId={1}
        onClose={() => {}}
        onSubmitted={() => {}}
      />,
    );

    const { fireEvent } = await import("@testing-library/dom");
    const typeSelectors = document.querySelectorAll(".ant-select-selector");
    fireEvent.mouseDown(typeSelectors[0]);
    const opt = await screen.findByText(/示例查询.*成功查询案例/);
    await user.click(opt);

    await user.type(await screen.findByLabelText("问题模式"), "Q1");
    // 涉及集合现在使用 DatabaseCollectionPicker (可选, 此测试不填)
    // userEvent.type 把 `{` 解析为修饰符, 需 `{{` 转义.
    await user.type(screen.getByLabelText("查询计划"), "{{ this is not json");

    await user.click(screen.getByRole("button", { name: /确定|OK/ }));

    expect(createKnowledge).not.toHaveBeenCalled();
    expect(await screen.findByText(/格式不合法/)).toBeInTheDocument();
  });


  it("rule 提交 → payload.applies_to_collections 为 CollectionRef[]", async () => {
    const { createKnowledge } = await import("@/api");
    (createKnowledge as any).mockResolvedValue({ entry: { id: 101 }, conflicts: [], overflow: false });
    const onSubmitted = vi.fn();
    const user = userEvent.setup();
    const { fireEvent } = await import("@testing-library/dom");
    render(
      <CreateKnowledgeForm
        open
        defaultNamespaceId={1}
        onClose={() => {}}
        onSubmitted={onSubmitted}
      />,
    );

    // 切到 rule 类型
    const typeSelectors = document.querySelectorAll(".ant-select-selector");
    fireEvent.mouseDown(typeSelectors[0]);
    const ruleOption = await screen.findByText(/查询规则.*查询约束/);
    await user.click(ruleOption);

    // 填写 rule_text
    await user.type(await screen.findByLabelText("规则文本"), "查订单按下单时间倒序");

    // 通过 DatabaseCollectionPicker 选择适用集合
    const dbInputs = screen.getAllByLabelText("数据库");
    const dbSelectEl = dbInputs[0].closest(".ant-select")?.querySelector(".ant-select-selector");
    expect(dbSelectEl).toBeTruthy();
    fireEvent.mouseDown(dbSelectEl!);
    const dbOption = await screen.findByText(/shop_db \(mongodb\)/);
    await user.click(dbOption);

    await new Promise((r) => setTimeout(r, 200));

    const collInputs = screen.getAllByLabelText(/集合|表/);
    const collSelectEl = collInputs[0].closest(".ant-select")?.querySelector(".ant-select-selector");
    expect(collSelectEl).toBeTruthy();
    fireEvent.mouseDown(collSelectEl!);
    const collOption = await screen.findByText("orders");
    await user.click(collOption);

    // 提交
    await user.click(screen.getByRole("button", { name: /确定|OK/ }));

    await waitFor(() => expect(createKnowledge).toHaveBeenCalledTimes(1));
    const body = (createKnowledge as any).mock.calls[0][0];
    expect(body.entry_type).toBe("rule");
    expect(body.payload.applies_to_collections).toEqual([{ database: "shop_db", collection: "orders" }]);
    expect(onSubmitted).toHaveBeenCalled();
  });

  it("example 提交 → payload.collections 为 CollectionRef[] (不再 split 文本)", async () => {
    const { createKnowledge } = await import("@/api");
    (createKnowledge as any).mockResolvedValue({ entry: { id: 102 }, conflicts: [], overflow: false });
    const onSubmitted = vi.fn();
    const user = userEvent.setup();
    const { fireEvent } = await import("@testing-library/dom");
    render(
      <CreateKnowledgeForm
        open
        defaultNamespaceId={1}
        onClose={() => {}}
        onSubmitted={onSubmitted}
      />,
    );

    // 切到 example 类型
    const typeSelectors = document.querySelectorAll(".ant-select-selector");
    fireEvent.mouseDown(typeSelectors[0]);
    const exOption = await screen.findByText(/示例查询.*成功查询案例/);
    await user.click(exOption);

    // 填写 question_pattern
    await user.type(await screen.findByLabelText("问题模式"), "按状态分组统计订单数");

    // 通过 DatabaseCollectionPicker 选择集合
    const dbInputs = screen.getAllByLabelText("数据库");
    const dbSelectEl = dbInputs[0].closest(".ant-select")?.querySelector(".ant-select-selector");
    expect(dbSelectEl).toBeTruthy();
    fireEvent.mouseDown(dbSelectEl!);
    const dbOption = await screen.findByText(/shop_db \(mongodb\)/);
    await user.click(dbOption);

    await new Promise((r) => setTimeout(r, 200));

    const collInputs = screen.getAllByLabelText(/集合|表/);
    const collSelectEl = collInputs[0].closest(".ant-select")?.querySelector(".ant-select-selector");
    expect(collSelectEl).toBeTruthy();
    fireEvent.mouseDown(collSelectEl!);
    const collOption = await screen.findByText("orders");
    await user.click(collOption);

    // 提交
    await user.click(screen.getByRole("button", { name: /确定|OK/ }));

    await waitFor(() => expect(createKnowledge).toHaveBeenCalledTimes(1));
    const body = (createKnowledge as any).mock.calls[0][0];
    expect(body.entry_type).toBe("example");
    expect(body.payload.collections).toEqual([{ database: "shop_db", collection: "orders" }]);
    expect(body.content).toBe("按状态分组统计订单数");
    expect(onSubmitted).toHaveBeenCalled();
  });

  it("instance_alias 渲染 db_type 选择器 (选项来自 DB_TYPE_META)", async () => {
    const user = userEvent.setup();
    const { fireEvent } = await import("@testing-library/dom");
    render(
      <CreateKnowledgeForm
        open
        defaultNamespaceId={1}
        onClose={() => {}}
        onSubmitted={() => {}}
      />,
    );

    // 切到 instance_alias 类型
    const typeSelectors = document.querySelectorAll(".ant-select-selector");
    fireEvent.mouseDown(typeSelectors[0]);
    const iaOption = await screen.findByText(/实例别名.*具体一条记录/);
    await user.click(iaOption);

    // db_type 选择器存在
    const dbTypeInputs = screen.getAllByLabelText("数据库类型");
    expect(dbTypeInputs.length).toBeGreaterThan(0);
  });

  it("instance_alias 缺 db_type 提交触发校验 warning — 不调 createKnowledge", async () => {
    const { createKnowledge } = await import("@/api");
    const user = userEvent.setup();
    const { fireEvent } = await import("@testing-library/dom");
    render(
      <CreateKnowledgeForm
        open
        defaultNamespaceId={1}
        onClose={() => {}}
        onSubmitted={() => {}}
      />,
    );

    // 切到 instance_alias
    const typeSelectors = document.querySelectorAll(".ant-select-selector");
    fireEvent.mouseDown(typeSelectors[0]);
    const iaOption = await screen.findByText(/实例别名.*具体一条记录/);
    await user.click(iaOption);

    // 只填别名和记录 ID, 不选库 (db_type 为空)
    await user.type(await screen.findByPlaceholderText(/用户问题里的简称/), "黄金会员");
    await user.type(screen.getByPlaceholderText("_id 或唯一键值"), "5f8a1b2c3d4e5f6a7b8c9d0e");

    await user.click(screen.getByRole("button", { name: /确定|OK/ }));

    expect(createKnowledge).not.toHaveBeenCalled();
    expect(await screen.findByText(/db_type 必填/)).toBeInTheDocument();
  });

  it("instance_alias 提交 → payload 含 db_type (选库自动同步)", async () => {
    const { createKnowledge } = await import("@/api");
    (createKnowledge as any).mockResolvedValue({ entry: { id: 103 }, conflicts: [], overflow: false });
    const onSubmitted = vi.fn();
    const user = userEvent.setup();
    const { fireEvent } = await import("@testing-library/dom");
    render(
      <CreateKnowledgeForm
        open
        defaultNamespaceId={1}
        onClose={() => {}}
        onSubmitted={onSubmitted}
      />,
    );

    // 切到 instance_alias
    const typeSelectors = document.querySelectorAll(".ant-select-selector");
    fireEvent.mouseDown(typeSelectors[0]);
    const iaOption = await screen.findByText(/实例别名.*具体一条记录/);
    await user.click(iaOption);

    // 填别名
    await user.type(await screen.findByPlaceholderText(/用户问题里的简称/), "黄金会员");

    // 选库 (onDbTypeChange 自动同步 db_type=mongodb) + 集合
    const dbInputs = screen.getAllByLabelText("数据库");
    const dbSelectEl = dbInputs[0].closest(".ant-select")?.querySelector(".ant-select-selector");
    expect(dbSelectEl).toBeTruthy();
    fireEvent.mouseDown(dbSelectEl!);
    const dbOption = await screen.findByText(/shop_db \(mongodb\)/);
    await user.click(dbOption);

    await new Promise((r) => setTimeout(r, 200));

    const collInputs = screen.getAllByLabelText(/集合|表/);
    const collSelectEl = collInputs[0].closest(".ant-select")?.querySelector(".ant-select-selector");
    expect(collSelectEl).toBeTruthy();
    fireEvent.mouseDown(collSelectEl!);
    const collOption = await screen.findByText("orders");
    await user.click(collOption);

    // 填记录 ID
    await user.type(screen.getByPlaceholderText("_id 或唯一键值"), "5f8a1b2c3d4e5f6a7b8c9d0e");

    // 提交
    await user.click(screen.getByRole("button", { name: /确定|OK/ }));

    await waitFor(() => expect(createKnowledge).toHaveBeenCalledTimes(1));
    const body = (createKnowledge as any).mock.calls[0][0];
    expect(body.entry_type).toBe("instance_alias");
    expect(body.payload.db_type).toBe("mongodb");
    expect(body.payload.target_database).toBe("shop_db");
    expect(body.payload.target_collection).toBe("orders");
    expect(body.payload.target_id).toBe("5f8a1b2c3d4e5f6a7b8c9d0e");
    expect(onSubmitted).toHaveBeenCalled();
  });

});
