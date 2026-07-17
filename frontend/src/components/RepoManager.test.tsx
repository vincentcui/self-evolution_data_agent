/**
 * RepoManager 顶部操作栏契约测试.
 *
 * 覆盖:
 * - 两行固定布局: Row2 批量/全量按钮始终渲染 (不再条件隐藏), 消除添加前后跳变
 * - repos 空时两按钮 disabled; pending/parsable 态各自 enabled
 * - placeholder/tooltip 文案 (http(s)+token, SSH 未启用提示)
 */
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { GitRepo } from "@/types";

// 隔离测试: mock @/api 模块, fetchProfiles mount 时调用需返回 Promise
vi.mock("@/api", () => ({
  fetchProfiles: vi.fn().mockResolvedValue([]),
  addRepo: vi.fn().mockResolvedValue({}),
  addRepoMapping: vi.fn().mockResolvedValue({}),
  batchParseRepos: vi.fn().mockResolvedValue({}),
  cancelParse: vi.fn().mockResolvedValue({}),
  deleteRepo: vi.fn().mockResolvedValue({}),
  deleteRepoMapping: vi.fn().mockResolvedValue({}),
  fetchRepoMappings: vi.fn().mockResolvedValue([]),
  getParseReport: vi.fn().mockResolvedValue({}),
  parseRepo: vi.fn().mockResolvedValue({}),
  testRepoReachability: vi.fn().mockResolvedValue({}),
  updateRepoProfile: vi.fn().mockResolvedValue({}),
}));

import RepoManager from "./RepoManager";

/** 最小 GitRepo 构造 (as cast 绕过完整字段). parse_status 枚举: pending|cloning|parsing|parsed|error */
function repo(over: Partial<GitRepo>): GitRepo {
  return {
    id: 1, url: "http://gitlab.example.com/u/r.git", branch: "master",
    parse_status: "parsed", worker_id: "",
    ...over,
  } as unknown as GitRepo;
}

function renderManager(repos: GitRepo[] = []) {
  render(
    <RepoManager
      nsId={1}
      datasources={[]}
      repos={repos}
      onReposChange={vi.fn()}
    />,
  );
}

describe("RepoManager 顶部操作栏", () => {
  it("repos 空: 批量/全量按钮均 disabled (不再消失, 消除布局跳变)", () => {
    renderManager([]);
    expect(screen.getByRole("button", { name: /批量解析/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /全量解析/ })).toBeDisabled();
  });

  it("pending 态: 批量解析 enabled", () => {
    renderManager([repo({ parse_status: "pending", worker_id: "" })]);
    expect(screen.getByRole("button", { name: /批量解析/ })).toBeEnabled();
  });

  it("error 态: 批量解析 enabled", () => {
    renderManager([repo({ parse_status: "error", worker_id: "" })]);
    expect(screen.getByRole("button", { name: /批量解析/ })).toBeEnabled();
  });

  it("parsed 无 worker: 全量解析 enabled, 批量 disabled", () => {
    renderManager([repo({})]);
    expect(screen.getByRole("button", { name: /全量解析/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /批量解析/ })).toBeDisabled();
  });

  it("parsing 有 worker: 两按钮均 disabled", () => {
    renderManager([repo({ parse_status: "parsing", worker_id: "w1" })]);
    expect(screen.getByRole("button", { name: /批量解析/ })).toBeDisabled();
    expect(screen.getByRole("button", { name: /全量解析/ })).toBeDisabled();
  });

  it("url placeholder 提示 http(s) 通用示例 (不绑 nse.cn)", () => {
    renderManager([]);
    expect(screen.getByPlaceholderText("https://gitlab.example.com/group/repo.git")).toBeTruthy();
  });

  it("git_token placeholder 提示 http(s) token", () => {
    renderManager([]);
    expect(screen.getByPlaceholderText("http(s) 私有库 access token")).toBeTruthy();
  });
});
