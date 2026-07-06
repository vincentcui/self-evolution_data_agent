/* ════════════════════════════════════════════
 *  useLastNamespaceId — 跨页命名空间选择记忆
 * ----------------------------------------------------------------------------
 *  按 currentUserId 分键存储, 切换用户互不串读. 当前登录用户 id 同步取自
 *  localStorage.user (AuthContext 写入); 未登录时落 guest 桶, 登录后自然切到
 *  用户专属桶. 任何页面切换命名空间后更新, 其他页面进入时优先恢复;
 *  找不到对应 ns 时调用方自行 fallback 到 list[0].
 * ════════════════════════════════════════════ */

/* 当前登录用户 id — 同步读 localStorage.user, 供分键用.
   AuthContext 在 login/刷新时写入; Layout 首次渲染时该值已就位. */
function currentUserId(): number | null {
  const raw = localStorage.getItem("user");
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return typeof parsed?.id === "number" ? parsed.id : null;
  } catch {
    return null;
  }
}

const storageKey = (): string => {
  const uid = currentUserId();
  return uid != null ? `lastNamespaceId:${uid}` : "lastNamespaceId:guest";
};

export function readLastNamespaceId(): number | undefined {
  const raw = localStorage.getItem(storageKey());
  if (!raw) return undefined;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) ? parsed : undefined;
}

export function writeLastNamespaceId(id: number): void {
  localStorage.setItem(storageKey(), String(id));
}

export function clearLastNamespaceId(): void {
  localStorage.removeItem(storageKey());
}
