import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { mergeReflection } from "./AgentTracesPage";

vi.mock("@/api", () => ({
  listAgentTraces: vi.fn(),
  getAgentTrace: vi.fn(async () => ({
    trace_id: "t-call-1",
    user_query: "订单数",
    trace_json: '{"tool_trace":[]}',
    reflection_log_json: "[]",
    tool_trace_compact: [
      { step: 0, tool: "fetch_schema", target: "c_orders", schema_field_count: 2 },
      { step: 1, tool: "execute_query", target: "c_orders", mode: "count", count_returned: 7 },
    ],
    status: "completed",
    refined_at: null,
    refined_summary: null,
    created_at: "2026-06-23",
  })),
  refineAgentTraces: vi.fn(),
}));

import { TraceDetailModal } from "./AgentTracesPage";

describe("TraceDetailModal 调用列表", () => {
  it("reflection 空: 渲染调用列表, 无 Confidence 列, 保留原始 JSON", async () => {
    render(<TraceDetailModal traceId="t-call-1" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText("fetch_schema")).toBeInTheDocument());
    // 调用列表两行都在
    expect(screen.getByText("execute_query")).toBeInTheDocument();
    // reflection 列不渲染
    expect(screen.queryByText("Confidence")).not.toBeInTheDocument();
    expect(screen.queryByText("Reason")).not.toBeInTheDocument();
    // 原始 Trace JSON 块仍在
    expect(screen.getByText(/Trace JSON/)).toBeInTheDocument();
  });
});

describe("mergeReflection (best-effort 序数匹配)", () => {
  const rows = [
    { step: 0, tool: "fetch_schema", target: "c_orders" },
    { step: 1, tool: "execute_query", target: "c_orders" },
    { step: 2, tool: "execute_query", target: "c_orders" },
  ] as any;

  it("reflection 空: rows 原样返回, 无 reflection 字段", () => {
    const out = mergeReflection(rows, []);
    expect(out[0].reflection).toBeUndefined();
    expect(out.length).toBe(3);
  });

  it("reflection 非空: 按 tool_name 序数匹配, 匹配不上无 reflection", () => {
    const reflections = [
      { tool_name: "execute_query", confidence: 0.8, reason: "r1", alternative: "" },
      { tool_name: "execute_query", confidence: 0.5, reason: "r2", alternative: "" },
    ];
    const out = mergeReflection(rows, reflections);
    expect(out[0].reflection).toBeUndefined();          // fetch_schema 无匹配
    expect(out[1].reflection?.confidence).toBe(0.8);    // 第1个 execute_query
    expect(out[2].reflection?.confidence).toBe(0.5);    // 第2个 execute_query
  });
});

describe("TraceDetailModal reflection 覆盖层", () => {
  it("reflection 非空: 三列出现, 匹配行有值, 不匹配行显 —", async () => {
    const { getAgentTrace } = await import("@/api");
    (getAgentTrace as any).mockResolvedValue({
      trace_id: "t-call-2",
      user_query: "q",
      trace_json: '{"tool_trace":[]}',
      reflection_log_json: JSON.stringify([
        { tool_name: "execute_query", confidence: 0.9, reason: "锚点命中", alternative: "fetch_schema" },
      ]),
      tool_trace_compact: [
        { step: 0, tool: "fetch_schema", target: "c_orders" },
        { step: 1, tool: "execute_query", target: "c_orders", mode: "count", count_returned: 7 },
      ],
      status: "completed", refined_at: null, refined_summary: null, created_at: "2026-06-23",
    });
    render(<TraceDetailModal traceId="t-call-2" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText("Confidence")).toBeInTheDocument());
    expect(screen.getByText("Reason")).toBeInTheDocument();
    expect(screen.getByText("0.90")).toBeInTheDocument();        // execute_query 匹配, confidence 渲染
    // fetch_schema 行无匹配 → Confidence 列显 —
    const dashes = screen.getAllByText("—");
    expect(dashes.length).toBeGreaterThanOrEqual(1);
  });

  it("reflection_log_json 非法 JSON: 调用列表正常渲染, 无 reflection 列, 不抛", async () => {
    const { getAgentTrace } = await import("@/api");
    (getAgentTrace as any).mockResolvedValue({
      trace_id: "t-call-3",
      user_query: "q",
      trace_json: '{"tool_trace":[]}',
      reflection_log_json: "not-json{",
      tool_trace_compact: [
        { step: 0, tool: "fetch_schema", target: "c_orders" },
      ],
      status: "completed", refined_at: null, refined_summary: null, created_at: "2026-06-23",
    });
    render(<TraceDetailModal traceId="t-call-3" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText("fetch_schema")).toBeInTheDocument());
    // 非法 JSON → reflections=[] → 无 reflection 列
    expect(screen.queryByText("Confidence")).not.toBeInTheDocument();
  });
});

describe("TraceDetailModal 入参/返回值列", () => {
  it("列标题为 入参/返回值 (非旧 查询摘要/结果)", async () => {
    const { getAgentTrace } = await import("@/api");
    (getAgentTrace as any).mockResolvedValue({
      trace_id: "t-io", user_query: "q", trace_json: "{}", reflection_log_json: "[]",
      tool_trace_compact: [
        { step: 0, tool: "fetch_schema", target: "c_orders", schema_field_count: 5 },
        { step: 1, tool: "lookup_knowledge", query: "订单规则", recalled_ke_ids: [1, 2] },
        { step: 2, tool: "execute_plan", plan_step_count: 2, plan_collections: ["orders", "items"], rows_returned: 7 },
      ],
      status: "completed", refined_at: null, refined_summary: null, created_at: "x",
    });
    render(<TraceDetailModal traceId="t-io" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText("入参")).toBeInTheDocument());
    expect(screen.getByText("返回值")).toBeInTheDocument();
    expect(screen.queryByText("查询摘要")).not.toBeInTheDocument();
    // fetch_schema: 入参=target, 返回值=5 字段
    expect(screen.getByText("5 字段")).toBeInTheDocument();
    // lookup_knowledge: 入参=查: 订单规则, 返回值=召回 2 条
    expect(screen.getByText("查: 订单规则")).toBeInTheDocument();
    expect(screen.getByText("召回 2 条")).toBeInTheDocument();
    // execute_plan: 入参=计划 2步, 返回值=7 行
    expect(screen.getByText("计划 2步 [orders,items]")).toBeInTheDocument();
    expect(screen.getByText("7 行")).toBeInTheDocument();
  });

  it("error 优先显在返回值列", async () => {
    const { getAgentTrace } = await import("@/api");
    (getAgentTrace as any).mockResolvedValue({
      trace_id: "t-err", user_query: "q", trace_json: "{}", reflection_log_json: "[]",
      tool_trace_compact: [{ step: 0, tool: "execute_query", error: "连接超时" }],
      status: "completed", refined_at: null, refined_summary: null, created_at: "x",
    });
    render(<TraceDetailModal traceId="t-err" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText(/连接超时/)).toBeInTheDocument());
  });

  it("展开调用行可见完整 input/output (非仅 compact 摘要)", async () => {
    const { getAgentTrace } = await import("@/api");
    (getAgentTrace as any).mockResolvedValue({
      trace_id: "t-expand", user_query: "q",
      // trace_json 原文含完整 input/output (compact 摘要不带 full_only_field)
      trace_json: JSON.stringify({
        tool_trace: [
          {
            name: "fetch_schema",
            input: { target: "c_orders", full_only_field: "DETAIL_XYZ_123" },
            output: { fields: [{ name: "oid", type: "int" }] },
          },
        ],
      }),
      reflection_log_json: "[]",
      tool_trace_compact: [{ step: 0, tool: "fetch_schema", target: "c_orders", schema_field_count: 1 }],
      status: "completed", refined_at: null, refined_summary: null, created_at: "x",
    });
    render(<TraceDetailModal traceId="t-expand" onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText("fetch_schema")).toBeInTheDocument());
    // 展开前: 无展开区独有标题
    expect(screen.queryByText("入参 (完整)")).not.toBeInTheDocument();
    // 点展开图标 (Modal 走 Portal 渲染到 document.body, 不在 render container)
    const expandIcon = document.body.querySelector(".ant-table-row-expand-icon") as HTMLButtonElement;
    expect(expandIcon).toBeTruthy();
    expandIcon.click();
    // 展开后: 完整 input/output 区出现 (标题 + 含 compact 没有的字段)
    await waitFor(() => expect(screen.getByText("入参 (完整)")).toBeInTheDocument());
    expect(screen.getByText("返回值 (完整)")).toBeInTheDocument();
    // 展开区 JSON 含原始 input 的 full_only_field (compact 摘要不带)
    const expanded = screen.getAllByText(/DETAIL_XYZ_123/);
    expect(expanded.length).toBeGreaterThanOrEqual(1);
  });
});
