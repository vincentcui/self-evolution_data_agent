/* ════════════════════════════════════════════════════════════════════════════
 *  L1 Contract: Example payload API — axios mock
 *  ────────────────────────────────────────────────────────────────────────
 *  Mocks axios at the module level to assert PUT /knowledge/{id} URL + 5-field
 *  body shape matches backend expectation.
 *
 *  WHY: Three React components updated (ExampleEditPanel / CreateKnowledgeForm /
 *  EditCanonicalForm). L1 contract test prevents "path wrong / body field name
 *  wrong" that single-component tests silently miss.
 * ══════════════════════════════════════════════════════════════════════════ */

import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import axios from "axios";

vi.mock("axios", () => {
  const mockAxios = {
    create: vi.fn(() => mockAxios),
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
    get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn(),
  };
  return { default: mockAxios };
});

const mockAxios = axios as unknown as { post: Mock; put: Mock };

describe("Example payload API contract (L1)", () => {
  beforeEach(() => vi.clearAllMocks());

  it("editKnowledge PUT matches URL /knowledge/{id} and 5-field body", async () => {
    mockAxios.put.mockResolvedValueOnce({ data: { entry: {}, conflicts: [] } });

    const { editKnowledge } = await import("@/api");
    await editKnowledge(42, {
      payload: {
        question_pattern: "按状态统计订单",
        collections: ["shop.orders"],
        join_keys: [{ from: "orders.uid", to: "users.id" }],
        final_query_plan: {
          steps: [{
            db_type: "mysql", collection: "orders", operation: "sql",
            query: { sql: "SELECT ..." },
          }],
        },
        result_summary: "分组统计",
      },
      content: "按状态统计订单",
      tier: "normal",
      reason: "test",
    });

    expect(mockAxios.put).toHaveBeenCalledTimes(1);
    const [url, body] = mockAxios.put.mock.calls[0];
    expect(url).toMatch(/\/knowledge\/42$/);
    expect(body.payload.question_pattern).toBe("按状态统计订单");
    expect(body.payload.collections).toEqual(["shop.orders"]);
    expect(body.payload.join_keys).toHaveLength(1);
    expect(body.payload.final_query_plan).toBeDefined();
    expect(body.payload.result_summary).toBe("分组统计");
  });

  it("createKnowledge POST body includes 5-field payload for example type", async () => {
    mockAxios.post.mockResolvedValueOnce({
      data: { entry: { id: 99 }, conflicts: [], overflow: false, split_candidates: [] },
    });

    const { createKnowledge } = await import("@/api");
    await createKnowledge({
      entry_type: "example",
      namespace_id: 1,
      tier: "normal",
      content: "查询订单",
      payload: {
        question_pattern: "查询订单",
        collections: ["shop.orders"],
        join_keys: [],
        final_query_plan: null,
        result_summary: "",
      },
    });

    expect(mockAxios.post).toHaveBeenCalledTimes(1);
    const [, body] = mockAxios.post.mock.calls[0];
    expect(body.payload.question_pattern).toBe("查询订单");
  });
});
