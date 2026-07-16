/* ════════════════════════════════════════════════════════════════════════════
 *  SchemaCanonicalPanel — Oracle 筛选选项单测
 * ══════════════════════════════════════════════════════════════════════════ */

import React from "react";
import { render, fireEvent, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { SchemaCanonicalPanel } from "@/components/SchemaCanonicalPanel";

// mock 所有 API 调用
vi.mock("@/api", () => ({
  schemaCanonicalApi: {
    listCanonicals: vi.fn().mockResolvedValue([]),
    getPendingCounts: vi.fn().mockResolvedValue({ pending_promote: 0, pending_candidates: 0 }),
    listExtractionFailures: vi.fn().mockResolvedValue([]),
    listPendingCandidates: vi.fn().mockResolvedValue([]),
    listConflicts: vi.fn().mockResolvedValue([]),
    listEvidenceOnlyFields: vi.fn().mockResolvedValue([]),
  },
  enumApi: {
    listEnumDictionaries: vi.fn().mockResolvedValue({ items: [], total: 0 }),
    listPendingEnumBindings: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  },
  getDatabases: vi.fn().mockResolvedValue({ databases: [] }),
  getCollections: vi.fn().mockResolvedValue({ collections: [], db_type: null }),
}));

beforeEach(() => vi.clearAllMocks());

describe("SchemaCanonicalPanel db_type 筛选", () => {
  it("筛选下拉包含 Oracle 选项", async () => {
    const { container } = render(<SchemaCanonicalPanel namespaceId={1} />);
    // AntD Select 需要 mouseDown 在 .ant-select-selector 上才能打开下拉
    // dropdown 渲染在 document.body portal 里，不在组件 subtree 内
    const selector = container.querySelector(".ant-select-selector");
    expect(selector).not.toBeNull();
    fireEvent.mouseDown(selector!);
    await waitFor(() => {
      const opts = Array.from(
        document.querySelectorAll(".ant-select-item-option-content"),
      ).map((el) => el.textContent);
      expect(opts).toContain("Oracle");
    });
  });
});
