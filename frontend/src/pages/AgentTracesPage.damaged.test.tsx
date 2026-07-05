import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

vi.mock("@/api", () => ({
  listAgentTraces: vi.fn(),
  getAgentTrace: vi.fn(),
  refineAgentTraces: vi.fn(),
}));
// NamespaceSelector 挂载即自动选中 ns, 触发额外 listAgentTraces — mock 掉防干扰
vi.mock("@/components/NamespaceSelector", () => ({
  default: () => null,
}));

import AgentTracesPage, { TraceDetailModal } from "./AgentTracesPage";
import { listAgentTraces, getAgentTrace } from "@/api";

const _row = (trace_id: string, trace_damaged: boolean, tool_call_count: number | null) => ({
  id: 1, trace_id, namespace_id: null, user_query: "q", status: "completed",
  created_at: "2026-07-05", tool_call_count, trace_damaged,
});

describe("trace_damaged 渲染", () => {
  it("列表: trace_damaged=true 时 Tools 列显示'损坏'标签", async () => {
    (listAgentTraces as ReturnType<typeof vi.fn>).mockResolvedValue([
      _row("t-damaged-1", true, null),
    ]);
    render(<AgentTracesPage />);
    await waitFor(() => expect(screen.getByText("t-damaged-1")).toBeInTheDocument());
    expect(screen.getByText("损坏")).toBeInTheDocument();
  });

  it("列表: trace_damaged=false 时 Tools 列正常显示 tool_call_count", async () => {
    (listAgentTraces as ReturnType<typeof vi.fn>).mockResolvedValue([
      _row("t-ok-1", false, 7),
    ]);
    render(<AgentTracesPage />);
    await waitFor(() => expect(screen.getByText("t-ok-1")).toBeInTheDocument());
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.queryByText("损坏")).not.toBeInTheDocument();
  });

  it("详情: trace_damaged=true 时顶部显示损坏提示", async () => {
    (getAgentTrace as ReturnType<typeof vi.fn>).mockResolvedValue({
      trace_id: "t-damaged-detail",
      user_query: "damaged",
      trace_json: '{"tool_trace": [{"name":"execute_query",',
      reflection_log_json: "[]",
      tool_trace_compact: [],
      status: "completed",
      refined_at: null,
      refined_summary: null,
      created_at: "2026-07-05",
      trace_damaged: true,
    });
    render(<TraceDetailModal traceId="t-damaged-detail" onClose={() => {}} />);
    await waitFor(() =>
      expect(screen.getByText(/数据存在但损坏/)).toBeInTheDocument(),
    );
  });
});
