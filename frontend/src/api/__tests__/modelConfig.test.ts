import { describe, it, expect, vi } from "vitest";
import { http } from "../index";
import { listModelConfigs, checkModelReady } from "../modelConfig";

vi.mock("../../../src/api/index", () => ({
  http: { get: vi.fn() },
}));

describe("listModelConfigs", () => {
  it("不传 namespaceId 时不带 query param", async () => {
    vi.mocked(http.get).mockResolvedValue({ data: [] } as any);
    await listModelConfigs();
    expect(http.get).toHaveBeenCalledWith("/model-config/list", { params: undefined });
  });

  it("传入 namespaceId 时 URL 含 query param", async () => {
    vi.mocked(http.get).mockResolvedValue({ data: [] } as any);
    await listModelConfigs(5);
    expect(http.get).toHaveBeenCalledWith("/model-config/list", {
      params: { namespace_id: 5 },
    });
  });
});

describe("checkModelReady", () => {
  it("传入 namespaceId 时 URL 含 query param", async () => {
    vi.mocked(http.get).mockResolvedValue({ data: { chat_model_ready: true, embedding_model_ready: true, ready: true } } as any);
    await checkModelReady(5);
    expect(http.get).toHaveBeenCalledWith("/model-config/check-ready", {
      params: { namespace_id: 5 },
    });
  });
});
