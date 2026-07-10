import { describe, it, expect, vi, beforeEach } from "vitest";

// L1 契约: mock 共享 axios 实例 → 断言真实 api/index.ts 产出的 URL + method + body
const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPut = vi.fn();
const mockDelete = vi.fn();

vi.mock("axios", () => ({
  default: {
    create: () => ({
      get: (...args: any[]) => mockGet(...args),
      post: (...args: any[]) => mockPost(...args),
      put: (...args: any[]) => mockPut(...args),
      delete: (...args: any[]) => mockDelete(...args),
      interceptors: {
        request: { use: vi.fn() },
        response: { use: vi.fn() },
      },
    }),
  },
}));

beforeEach(() => {
  vi.clearAllMocks();
  mockGet.mockResolvedValue({ data: [] });
  mockPost.mockResolvedValue({ data: { id: 1 } });
  mockPut.mockResolvedValue({ data: { id: 1 } });
  mockDelete.mockResolvedValue({ data: {} });
});

describe("GitTokenConfig API L1 — URL + method + body contract", () => {
  it("fetchGitTokenConfigs calls GET /git-token-config/list", async () => {
    const { fetchGitTokenConfigs } = await import("../api");
    await fetchGitTokenConfigs();
    expect(mockGet).toHaveBeenCalledWith("/git-token-config/list");
  });

  it("addGitTokenConfig POSTs to /git-token-config/add", async () => {
    const { addGitTokenConfig } = await import("../api");
    await addGitTokenConfig({ name: "GitHub PAT", token: "ghp_xxx", description: "test" });
    expect(mockPost).toHaveBeenCalledWith(
      "/git-token-config/add",
      expect.objectContaining({ name: "GitHub PAT", token: "ghp_xxx" }),
    );
  });

  it("updateGitTokenConfig PUTs to /git-token-config/update", async () => {
    const { updateGitTokenConfig } = await import("../api");
    await updateGitTokenConfig({ id: 1, name: "Updated", token: "****" });
    expect(mockPut).toHaveBeenCalledWith(
      "/git-token-config/update",
      expect.objectContaining({ id: 1, name: "Updated" }),
    );
  });

  it("deleteGitTokenConfig DELETEs /git-token-config/{id}", async () => {
    const { deleteGitTokenConfig } = await import("../api");
    await deleteGitTokenConfig(5);
    expect(mockDelete).toHaveBeenCalledWith("/git-token-config/5");
  });

  it("activateGitTokenConfig POSTs to /git-token-config/activate/{id}", async () => {
    const { activateGitTokenConfig } = await import("../api");
    await activateGitTokenConfig(3);
    expect(mockPost).toHaveBeenCalledWith("/git-token-config/activate/3");
  });

  it("testGitTokenConfig POSTs to /git-token-config/test", async () => {
    const { testGitTokenConfig } = await import("../api");
    await testGitTokenConfig({ id: 1, url: "https://github.com/org/repo.git" });
    expect(mockPost).toHaveBeenCalledWith(
      "/git-token-config/test",
      expect.objectContaining({ id: 1, url: "https://github.com/org/repo.git" }),
    );
  });
});

describe("testRepoReachability API L1 — URL + method + body contract", () => {
  it("testRepoReachability POSTs to /namespaces/{nsId}/repos/test-reachability", async () => {
    const { testRepoReachability } = await import("../api");
    await testRepoReachability(7, { url: "https://github.com/test/repo.git", git_token: "ghp_xxx" });
    expect(mockPost).toHaveBeenCalledWith(
      "/namespaces/7/repos/test-reachability",
      expect.objectContaining({ url: "https://github.com/test/repo.git", git_token: "ghp_xxx" }),
    );
  });
});
