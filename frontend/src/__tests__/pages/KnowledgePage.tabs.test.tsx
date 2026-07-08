/* ════════════════════════════════════════════════════════════════════════════
 *  KnowledgePage — 知识条目 tab 切到 AuditQueue 后调 fetchAuditQueue 不再走 fetchKnowledge 渲染列表
 * ══════════════════════════════════════════════════════════════════════════ */

import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import KnowledgePage from "@/pages/KnowledgePage";

vi.mock("@/api", () => ({
  fetchNamespaces: vi.fn().mockResolvedValue([{ id: 1, name: "默认", slug: "default" }]),
  fetchKnowledge: vi.fn().mockResolvedValue([]),
  fetchRepos: vi.fn().mockResolvedValue({ repos: [] }),
  fetchDataSources: vi.fn().mockResolvedValue([]),
  listTerminologyConflicts: vi.fn().mockResolvedValue({ conflicts: [] }),
  fetchAuditQueue: vi.fn().mockResolvedValue({ items: [], total: 0, page: 1, size: 20 }),
  createKnowledge: vi.fn(),
  getDatabases: vi.fn().mockResolvedValue({ databases: [] }),
  getCollections: vi.fn().mockResolvedValue({ collections: [], db_type: null }),
  patchKnowledge: vi.fn(),
  deleteKnowledge: vi.fn(),
  supersedeKnowledge: vi.fn(),
  addRepo: vi.fn(),
  parseRepo: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  // NamespaceSelector 会把默认 ns 写入 localStorage, 各 test 保持隔离
  localStorage.clear();
});

describe("KnowledgePage 知识条目 tab", () => {
  it("默认进入 namespace 后, 知识条目 tab 调 fetchAuditQueue (不传 status)", async () => {
    const { fetchAuditQueue } = await import("@/api");
    render(<KnowledgePage />);

    // NamespaceSelector 挂载后自动选中第一个命名空间 (无 localStorage 记忆时),
    // 知识条目 tab 默认激活 → AuditQueue 加载, fetchAuditQueue 被调用
    await waitFor(() => expect(fetchAuditQueue).toHaveBeenCalled());
    const params = (fetchAuditQueue as any).mock.calls[0][0];
    expect(params.namespace_id).toBe(1);
    // showStatusFilter=true 但内部 statusFilter 默认 undefined → 不传 status
    expect(params.status).toBeUndefined();
    // 未使用: screen 仍需要保证未使用的引用被消除 (避免 lint 误报)
    expect(screen).toBeTruthy();
  });
});
