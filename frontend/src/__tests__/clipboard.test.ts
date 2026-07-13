// copyText — 安全上下文自适应剪贴板写入的降级链验证
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { copyText } from "../utils/clipboard";

describe("copyText", () => {
  const originalClipboard = navigator.clipboard;
  const originalIsSecure = window.isSecureContext;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    // 还原被改写的全局属性, 避免用例间污染
    Object.defineProperty(navigator, "clipboard", {
      value: originalClipboard,
      configurable: true,
    });
    Object.defineProperty(window, "isSecureContext", {
      value: originalIsSecure,
      configurable: true,
    });
  });

  function setSecureContext(secure: boolean) {
    Object.defineProperty(window, "isSecureContext", {
      value: secure,
      configurable: true,
    });
  }

  function setClipboard(writeText: unknown) {
    Object.defineProperty(navigator, "clipboard", {
      value: writeText ? { writeText } : undefined,
      configurable: true,
    });
  }

  // ── 路径 1: 安全上下文, 原生 API 成功 ──
  it("uses navigator.clipboard in a secure context", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    setClipboard(writeText);
    setSecureContext(true);

    const ok = await copyText("hello");

    expect(ok).toBe(true);
    expect(writeText).toHaveBeenCalledWith("hello");
  });

  // ── 路径 2: 非安全上下文 (明文 HTTP), clipboard 为 undefined → execCommand 兜底 ──
  it("falls back to execCommand when clipboard is unavailable (insecure context)", async () => {
    setClipboard(undefined);
    setSecureContext(false);
    const exec = vi.fn().mockReturnValue(true);
    document.execCommand = exec as unknown as typeof document.execCommand;

    const ok = await copyText("world");

    expect(ok).toBe(true);
    expect(exec).toHaveBeenCalledWith("copy");
  });

  // ── 路径 2b: 安全上下文但原生 API 抛错 → 同样降级到 execCommand ──
  it("falls back to execCommand when navigator.clipboard rejects", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("denied"));
    setClipboard(writeText);
    setSecureContext(true);
    const exec = vi.fn().mockReturnValue(true);
    document.execCommand = exec as unknown as typeof document.execCommand;

    const ok = await copyText("retry");

    expect(ok).toBe(true);
    expect(writeText).toHaveBeenCalled();
    expect(exec).toHaveBeenCalledWith("copy");
  });

  // ── 全失败: clipboard 缺失且 execCommand 返回 false ──
  it("returns false when every path fails", async () => {
    setClipboard(undefined);
    setSecureContext(false);
    const exec = vi.fn().mockReturnValue(false);
    document.execCommand = exec as unknown as typeof document.execCommand;

    const ok = await copyText("nope");

    expect(ok).toBe(false);
  });
});
