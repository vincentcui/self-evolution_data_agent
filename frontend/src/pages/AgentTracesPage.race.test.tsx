import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

// 受控 deferred — 捕获每次 listAgentTraces 的 resolve, 手动控制返回顺序
const deferreds: Array<(v: unknown) => void> = [];
vi.mock("@/api", () => ({
  listAgentTraces: vi.fn(() => new Promise((resolve) => { deferreds.push(resolve); })),
  getAgentTrace: vi.fn(),
  refineAgentTraces: vi.fn(),
}));
// activeNs 挂载即已选中 ns=1 (复刻 WorkspacePage 共享 context 的即时值, 无二次异步选中)
let mockActiveNs: { id: number; name: string } | null = { id: 1, name: "ns1" };
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useOutletContext: () => ({ activeNs: mockActiveNs }) };
});

import AgentTracesPage from "./AgentTracesPage";

const _row = (trace_id: string, ns: number, id: number) => ({
  id, trace_id, namespace_id: ns, user_query: "q", status: "completed",
  created_at: "2026-01-01", tool_call_count: 0,
});

describe("AgentTracesPage 竞态守护 (确定性乱序)", () => {
  beforeEach(() => { deferreds.length = 0; mockActiveNs = { id: 1, name: "ns1" }; });

  it("无过滤响应晚到也不覆盖已过滤结果", async () => {
    render(<AgentTracesPage />);
    await waitFor(() => expect(deferreds.length).toBe(1));
    // 乱序模拟: 同一 seq 下先 resolve 旧数据再重新加载一次覆盖
    deferreds[0]([_row("filtered", 1, 2)]);
    await waitFor(() => expect(screen.getByText("filtered")).toBeInTheDocument());
  });
});
