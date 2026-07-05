/* ════════════════════════════════════════════
 *  Stage 2 抓手 E — Agent Traces 运维页面
 *  含 Stage 2 抓手 C: reflection_log 详情 Modal
 * ════════════════════════════════════════════ */

import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Button, Modal, Select, Space, Table, Tag, message } from "antd";
import type { ColumnsType } from "antd/es/table";
import { getAgentTrace, listAgentTraces } from "@/api";
import NamespaceSelector from "@/components/NamespaceSelector";

interface TraceRow {
  id: number;
  trace_id: string;
  namespace_id: number | null;
  user_query: string;
  status: string;
  created_at: string;
  tool_call_count: number | null;
  trace_damaged?: boolean;
}

interface ReflectionEntry {
  tool_name: string;
  confidence: number | null;
  reason: string;
  alternative?: string;
}

interface CompactCall {
  step: number;
  tool: string;
  target?: string;
  mode?: string;
  pipeline_signature?: string;
  sql_signature?: string;
  filter_fields?: string[];
  rows_returned?: number;
  count_returned?: number;
  empty_result?: boolean;
  error?: string;
  schema_field_count?: number;
  // ── Task 2 (入参/返回值) 新增字段 ──
  field?: string;
  query?: string;
  database?: string;
  db_type?: string;
  plan_collections?: string[];
  plan_step_count?: number;
  plan_strategy?: string;
  chart_type?: string;
  category_column?: string;
  entry_type?: string;
  question?: string;
  user_answer?: string;
  distinct_count?: number;
  recalled_ke_ids?: unknown[];
  db_count?: number;
  table_count?: number;
  est_rows?: number;
  blocked?: boolean;
  truncated?: boolean;
  [key: string]: unknown;
}

// 调用列表行 = compact 行 + 可选 reflection 覆盖层 (mergeReflection 产出)
type CallRow = CompactCall & { reflection?: ReflectionEntry };

// expandable 行展开的完整 input/output JSON 块样式
const rawPreStyle: CSSProperties = {
  maxHeight: 320,
  overflow: "auto",
  fontSize: 12,
  background: "#fafafa",
  padding: 8,
  margin: "4px 0 0",
  whiteSpace: "pre-wrap",
  wordBreak: "break-all",
};

interface TraceDetail {
  trace_id: string;
  user_query: string;
  trace_json: string;
  reflection_log_json: string;
  tool_trace_compact: CompactCall[];
  status: string;
  refined_at: string | null;
  refined_summary: string | null;
  created_at: string;
  trace_damaged?: boolean;
}

function renderInput(c: CompactCall): string {
  switch (c.tool) {
    case "fetch_schema":
      return c.target || "—";
    case "execute_query": {
      const parts: string[] = [];
      if (c.mode) parts.push(`mode=${c.mode}`);
      if (c.sql_signature) parts.push(c.sql_signature);
      else if (c.pipeline_signature) parts.push(c.pipeline_signature);
      else if (c.filter_fields?.length) parts.push(`$match(${c.filter_fields.join(",")})`);
      return parts.length ? parts.join("; ") : "—";
    }
    case "inspect_values":
      return c.field ? `field=${c.field}` : "—";
    case "lookup_knowledge":
      return c.query ? `查: ${c.query}` : "—";
    case "list_databases":
      return "—";
    case "list_tables":
      return c.database ? `db=${c.database}` : "—";
    case "clarify_with_user":
      return c.question || "—";
    case "generate_query_plan":
      return c.plan_collections?.length ? `规划 [${c.plan_collections.join(",")}]` : "—";
    case "execute_plan":
      return c.plan_step_count
        ? `计划 ${c.plan_step_count}步 [${(c.plan_collections || []).join(",")}]`
        : "—";
    case "present_result":
      return c.chart_type ? `图表: ${c.chart_type}` : "—";
    case "estimate_cost":
      return c.target ? `${c.db_type || ""} ${c.target}`.trim() : "—";
    case "save_knowledge":
      return c.entry_type || "—";
    default:
      return "—";
  }
}

function renderOutput(c: CompactCall): string {
  if (c.error) return `错误: ${c.error.slice(0, 40)}`;
  switch (c.tool) {
    case "fetch_schema":
      return typeof c.schema_field_count === "number" ? `${c.schema_field_count} 字段` : "—";
    case "execute_query":
      if (typeof c.rows_returned === "number")
        return `${c.rows_returned} 行${c.empty_result ? " (空)" : ""}`;
      if (typeof c.count_returned === "number") return `count=${c.count_returned}`;
      return "—";
    case "inspect_values":
      return typeof c.distinct_count === "number" ? `${c.distinct_count} 个不同值` : "—";
    case "lookup_knowledge":
      return c.recalled_ke_ids?.length ? `召回 ${c.recalled_ke_ids.length} 条` : "—";
    case "list_databases":
      return typeof c.db_count === "number" ? `${c.db_count} 个库` : "—";
    case "list_tables":
      return typeof c.table_count === "number" ? `${c.table_count} 个表` : "—";
    case "clarify_with_user":
      return c.user_answer || "—";
    case "generate_query_plan":
      return c.plan_strategy ? `${c.plan_strategy} (${c.plan_step_count ?? 0}步)` : "—";
    case "execute_plan":
      return typeof c.rows_returned === "number"
        ? `${c.rows_returned} 行${c.truncated ? " (截断)" : ""}` : "—";
    case "present_result":
      return c.chart_type ? `渲染 ${c.chart_type}` : "—";
    case "estimate_cost":
      return typeof c.est_rows === "number" ? `估 ${c.est_rows} 行${c.blocked ? " 阻断" : ""}` : "—";
    case "save_knowledge":
      return "已保存";
    default:
      return "—";
  }
}

/* ---- reflection 覆盖层: best-effort 序数匹配 ---- */
export function mergeReflection(
  rows: CompactCall[],
  reflections: ReflectionEntry[],
): CallRow[] {
  if (!reflections.length) return rows;
  const byName = new Map<string, ReflectionEntry[]>();
  for (const r of reflections) {
    const arr = byName.get(r.tool_name) ?? [];
    arr.push(r);
    byName.set(r.tool_name, arr);
  }
  const counters = new Map<string, number>();
  return rows.map((row) => {
    const arr = byName.get(row.tool);
    if (!arr?.length) return row;
    const idx = Math.min(counters.get(row.tool) ?? 0, arr.length - 1);
    counters.set(row.tool, (counters.get(row.tool) ?? 0) + 1);
    return { ...row, reflection: arr[idx] };
  });
}

export function TraceDetailModal({
  traceId,
  onClose,
}: {
  traceId: string;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<TraceDetail | null>(null);

  useEffect(() => {
    getAgentTrace(traceId).then(setDetail).catch(() => message.error("加载详情失败"));
  }, [traceId]);

  if (!detail) return null;

  let reflections: ReflectionEntry[] = [];
  try {
    reflections = JSON.parse(detail.reflection_log_json || "[]");
  } catch {
    /* ignore — 非法 JSON 当作空 */
  }
  const callRows = mergeReflection(detail.tool_trace_compact, reflections);
  const hasReflection = reflections.length > 0;

  // 原始 tool_trace (来自 trace_json 原文) — expandable 行展开看完整 input/output.
  // step 索引与 compact 对齐 (compact 按 enumerate(tool_trace) 顺序产出).
  let rawToolTrace: unknown[] = [];
  try {
    const tj = JSON.parse(detail.trace_json || "{}");
    if (tj && typeof tj === "object" && Array.isArray((tj as Record<string, unknown>).tool_trace)) {
      rawToolTrace = (tj as Record<string, unknown[]>).tool_trace;
    }
  } catch {
    /* ignore — 非法 JSON 当作无原始数据 */
  }

  return (
    <Modal
      open
      onCancel={onClose}
      footer={null}
      title={`Trace: ${traceId}`}
      width={1120}
    >
      <p>
        <strong>Query:</strong> {detail.user_query}
      </p>
      <p>
        <strong>Status:</strong> <Tag>{detail.status}</Tag>
      </p>

      {detail.trace_damaged && (
        <div style={{ marginBottom: 12 }}>
          <Tag color="red">数据存在但损坏</Tag>
          <span style={{ color: "#888", fontSize: 12 }}>
            trace_json JSON 解析失败, 调用列表可能不完整
          </span>
        </div>
      )}

      <h4>调用列表 ({detail.tool_trace_compact.length})</h4>
      <div style={{ overflowX: "auto" }}>
      <Table
        size="small"
        dataSource={callRows}
        rowKey={(r) => r.step}
        pagination={false}
        expandable={{
          rowExpandable: () => rawToolTrace.length > 0,
          expandedRowRender: (r: CompactCall) => {
            const raw = rawToolTrace[r.step] as
              | { input?: unknown; output?: unknown }
              | undefined;
            return (
              <div style={{ display: "flex", gap: 16, padding: "4px 0" }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <strong>入参 (完整)</strong>
                  <pre style={rawPreStyle}>
                    {JSON.stringify(raw?.input ?? null, null, 2)}
                  </pre>
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <strong>返回值 (完整)</strong>
                  <pre style={rawPreStyle}>
                    {JSON.stringify(raw?.output ?? null, null, 2)}
                  </pre>
                </div>
              </div>
            );
          },
        }}
        columns={[
          { title: "#", dataIndex: "step", width: 50 },
          { title: "Tool", dataIndex: "tool", width: 130, ellipsis: true },
          { title: "Target", dataIndex: "target", width: 120, ellipsis: true,
            render: (v: string) => v || "—" },
          { title: "入参", ellipsis: true,
            render: (_: unknown, r: CompactCall) => renderInput(r) },
          { title: "返回值", ellipsis: true,
            render: (_: unknown, r: CompactCall) => renderOutput(r) },
          ...(hasReflection ? [
            { title: "Confidence", width: 90, ellipsis: true,
              render: (_: unknown, r: CallRow) => {
                const v = r.reflection?.confidence;
                return v !== undefined && v !== null ? v.toFixed(2) : "—";
              } },
            { title: "Reason", ellipsis: true,
              render: (_: unknown, r: CallRow) => r.reflection?.reason || "—" },
            { title: "Alternative", ellipsis: true,
              render: (_: unknown, r: CallRow) => r.reflection?.alternative || "—" },
          ] : []),
        ]}
      />
      </div>

      <h4 style={{ marginTop: 16 }}>Trace JSON</h4>
      <pre style={{ maxHeight: 400, overflow: "auto", fontSize: 12 }}>
        {detail.trace_json}
      </pre>
    </Modal>
  );
}

export default function AgentTracesPage() {
  const [rows, setRows] = useState<TraceRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [namespaceId, setNamespaceId] = useState<number | undefined>();
  const [statusFilter, setStatusFilter] = useState<string | undefined>();
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null);
  const [selectedRowKeys, setSelectedRowKeys] = useState<string[]>([]);
  const [refining, setRefining] = useState(false);
  const reqSeq = useRef(0);

  const load = async () => {
    const seq = ++reqSeq.current;
    setLoading(true);
    try {
      const data = await listAgentTraces({ namespace_id: namespaceId, status: statusFilter, size: 100 });
      if (seq === reqSeq.current) setRows(data);
    } catch {
      if (seq === reqSeq.current) message.error("加载 traces 失败");
    } finally {
      if (seq === reqSeq.current) setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [statusFilter, namespaceId]);

  const handleRefine = async () => {
    if (selectedRowKeys.length === 0) {
      message.warning("请先选择要提炼的 traces");
      return;
    }
    setRefining(true);
    try {
      const { refineAgentTraces } = await import("@/api");
      const res = await refineAgentTraces(selectedRowKeys);
      message.success(`提炼完成: 产生 ${res.proposed_count} 条知识提案`);
      setSelectedRowKeys([]);
      load();
    } catch {
      message.error("批量提炼失败");
    } finally {
      setRefining(false);
    }
  };

  const columns: ColumnsType<TraceRow> = [
    { title: "Trace ID", dataIndex: "trace_id", width: 220, ellipsis: true },
    { title: "Query", dataIndex: "user_query", ellipsis: true },
    {
      title: "Status",
      dataIndex: "status",
      width: 100,
      render: (s: string) => (
        <Tag color={s === "completed" ? "green" : s === "failed" ? "red" : "default"}>
          {s}
        </Tag>
      ),
    },
    { title: "Tools", dataIndex: "tool_call_count", width: 70,
      render: (count: number | null, row: TraceRow) =>
        row.trace_damaged
          ? <Tag color="red">损坏</Tag>
          : (count ?? "-"),
    },
    { title: "Created", dataIndex: "created_at", width: 180 },
    {
      title: "Action",
      width: 80,
      render: (_, row) => (
        <Button size="small" type="link" onClick={() => setSelectedTraceId(row.trace_id)}>
          详情
        </Button>
      ),
    },
  ];

  return (
    <div style={{ padding: 24 }}>
      <h2>Agent Traces</h2>
      <Space style={{ marginBottom: 16 }}>
        <NamespaceSelector
          style={{ width: 160 }}
          value={namespaceId}
          onChange={(id) => setNamespaceId(id)}
        />
        <Select
          allowClear
          placeholder="Status"
          style={{ width: 140 }}
          value={statusFilter}
          onChange={setStatusFilter}
          options={[
            { label: "completed", value: "completed" },
            { label: "failed", value: "failed" },
            { label: "cancelled", value: "cancelled" },
            { label: "refined", value: "refined" },
          ]}
        />
        <Button onClick={load}>刷新</Button>
        <Button
          type="primary"
          disabled={selectedRowKeys.length === 0}
          loading={refining}
          onClick={handleRefine}
        >
          批量提炼 ({selectedRowKeys.length})
        </Button>
      </Space>
      <Table
        size="small"
        loading={loading}
        dataSource={rows}
        columns={columns}
        rowKey="trace_id"
        pagination={{ pageSize: 50 }}
        rowSelection={{
          selectedRowKeys,
          onChange: (keys) => setSelectedRowKeys(keys as string[]),
          getCheckboxProps: (row) => ({
            disabled: row.status !== "completed",
          }),
        }}
      />
      {selectedTraceId && (
        <TraceDetailModal
          traceId={selectedTraceId}
          onClose={() => setSelectedTraceId(null)}
        />
      )}
    </div>
  );
}
