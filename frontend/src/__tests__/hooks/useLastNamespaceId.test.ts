/* ════════════════════════════════════════════
 *  useLastNamespaceId — 按 user 分键, 切换用户不串读
 * ════════════════════════════════════════════ */

import { beforeEach, describe, it, expect } from "vitest";
import {
  readLastNamespaceId,
  writeLastNamespaceId,
  clearLastNamespaceId,
} from "@/hooks/useLastNamespaceId";

const setUser = (id: number | null) => {
  if (id == null) localStorage.removeItem("user");
  else localStorage.setItem("user", JSON.stringify({ id, username: `u${id}` }));
};

beforeEach(() => {
  localStorage.clear();
});

describe("useLastNamespaceId — 按 user 分键", () => {
  it("同一用户 write→read 往返一致", () => {
    setUser(1);
    writeLastNamespaceId(136);
    expect(readLastNamespaceId()).toBe(136);
  });

  it("切换用户后读不到上个用户的选择 (Bug1 回归锚点)", () => {
    setUser(1);
    writeLastNamespaceId(136);
    setUser(2);
    expect(readLastNamespaceId()).toBeUndefined();
  });

  it("两用户各自的选择互不覆盖", () => {
    setUser(1);
    writeLastNamespaceId(136);
    setUser(2);
    writeLastNamespaceId(200);
    setUser(1);
    expect(readLastNamespaceId()).toBe(136);
    setUser(2);
    expect(readLastNamespaceId()).toBe(200);
  });

  it("clear 只清当前用户桶, 不影响其他用户", () => {
    setUser(1);
    writeLastNamespaceId(136);
    setUser(2);
    writeLastNamespaceId(200);
    clearLastNamespaceId();
    expect(readLastNamespaceId()).toBeUndefined();
    setUser(1);
    expect(readLastNamespaceId()).toBe(136);
  });

  it("未登录 (无 user) 落 guest 桶, 不抛", () => {
    setUser(null);
    writeLastNamespaceId(7);
    expect(readLastNamespaceId()).toBe(7);
  });
});
