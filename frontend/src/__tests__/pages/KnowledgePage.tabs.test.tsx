/* ════════════════════════════════════════════════════════════════════════════
 *  KnowledgePage — 知识条目 tab 切到 AuditQueue 后调 fetchAuditQueue 不再走 fetchKnowledge 渲染列表
 * ══════════════════════════════════════════════════════════════════════════ */

import { render, screen, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import KnowledgePage from "@/pages/KnowledgePage";

vi.mock("@/api", () => ({
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
  schemaCanonicalApi: {
    listCanonicals: vi.fn().mockResolvedValue([]),
    listExtractionFailures: vi.fn().mockResolvedValue([]),
  },
}));

// activeNs 走 WorkspacePage 共享 context (取代 NamespaceSelector 自动选中)
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return {
    ...actual,
    useOutletContext: () => ({ activeNs: { id: 1, name: "默认", slug: "default" } }),
  };
});

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
});

describe("KnowledgePage 知识条目 tab", () => {
  it("默认进入 namespace 后, 知识条目 tab 调 fetchAuditQueue (不传 status)", async () => {
    const { fetchAuditQueue } = await import("@/api");
    render(<KnowledgePage />);

    // activeNs 已由共享 context 提供, 知识条目 tab 默认激活 → AuditQueue 加载, fetchAuditQueue 被调用
    await waitFor(() => expect(fetchAuditQueue).toHaveBeenCalled());
    const params = (fetchAuditQueue as any).mock.calls[0][0];
    expect(params.namespace_id).toBe(1);
    // showStatusFilter=true 但内部 statusFilter 默认 undefined → 不传 status
    expect(params.status).toBeUndefined();
    // 未使用: screen 仍需要保证未使用的引用被消除 (避免 lint 误报)
    expect(screen).toBeTruthy();
  });

  it("渲染左侧分组导航 (NAV_GROUPS 4 组) + 空间准备度概览", async () => {
    render(<KnowledgePage />);

    // 空间准备度概览区 (activeNs 由共享 context 提供 → 始终渲染)
    await waitFor(() => expect(screen.getByText("空间准备度")).toBeInTheDocument());
    // 4 张准备度卡片 (数据源 / 知识审核 / 可问数 唯一; 训练来源与导航组标题重名故另断言)
    expect(screen.getByText("数据源")).toBeInTheDocument();
    expect(screen.getByText("知识审核")).toBeInTheDocument();
    expect(screen.getByText("可问数")).toBeInTheDocument();

    // 左侧导航 4 个分组标题 (知识资产 / 待处理 / 历史与异常 唯一)
    expect(screen.getByText("知识资产")).toBeInTheDocument();
    expect(screen.getByText("待处理")).toBeInTheDocument();
    expect(screen.getByText("历史与异常")).toBeInTheDocument();
    // "训练来源" 既是导航组标题又是准备度卡片 label → 出现 2 次
    expect(screen.getAllByText("训练来源")).toHaveLength(2);

    // 导航子项 label 抽样验证 (数据驱动 NAV_GROUPS 渲染成按钮)
    expect(screen.getByText("知识条目")).toBeInTheDocument();
    expect(screen.getByText("Schema 管理")).toBeInTheDocument();
    expect(screen.getByText("抽取失败")).toBeInTheDocument();
  });
});
