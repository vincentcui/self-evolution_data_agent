import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import axios from "axios";
import { getAgentTrace } from "@/api";

vi.mock("axios", () => {
  const mockAxios = {
    create: vi.fn(() => mockAxios),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
    get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(),
  };
  return { default: mockAxios };
});

const mockAxios = axios as unknown as { create: Mock; get: Mock; post: Mock; put: Mock; delete: Mock };

describe("getAgentTrace (L1 axios 契约)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("GET /agent-traces/{traceId} 且 r.data 原样透传 (含 tool_trace_compact)", async () => {
    const payload = {
      trace_id: "t-1", user_query: "q", trace_json: "{}", reflection_log_json: "[]",
      tool_trace_compact: [{ step: 0, tool: "fetch_schema" }],
      status: "completed", refined_at: null, refined_summary: null, created_at: "x",
    };
    mockAxios.get.mockResolvedValueOnce({ data: payload });
    const res = await getAgentTrace("t-1");
    expect(res).toEqual(payload);
    expect(mockAxios.get).toHaveBeenCalledWith("/agent-traces/t-1");
  });
});
