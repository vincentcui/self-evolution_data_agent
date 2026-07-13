/* ════════════════════════════════════════════
 *  copyText — 安全上下文自适应的剪贴板写入
 * ----------------------------------------------------------------------------
 *  navigator.clipboard 仅存在于 secure context (https / localhost)。
 *  明文 HTTP + 非 localhost (如通过 IP 访问的内网部署) 下该对象为 undefined,
 *  直接调用会抛 TypeError。此处提供两级降级:
 *    1. navigator.clipboard.writeText   —— 安全上下文首选
 *    2. document.execCommand('copy')     —— 明文 HTTP 兜底 (API 已废弃但全兼容)
 *  返回 boolean 供调用方决定 toast 文案, 真实异常记入 console 不再静默吞掉。
 * ════════════════════════════════════════════ */

/**
 * 将文本写入剪贴板, 自动在非安全上下文降级到 execCommand。
 * @returns 复制成功返回 true, 全部路径失败返回 false。
 */
export async function copyText(text: string): Promise<boolean> {
  // ── 路径 1: 安全上下文原生 API ──
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      // 安全上下文下仍失败 (权限/焦点问题), 落到 execCommand 再试一次
      console.warn("[copyText] navigator.clipboard failed, falling back:", e);
    }
  }

  // ── 路径 2: execCommand 兜底 (明文 HTTP / 老浏览器) ──
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    // 移出视口 + 只读, 避免闪烁与软键盘弹出
    ta.style.position = "fixed";
    ta.style.top = "-9999px";
    ta.style.left = "-9999px";
    ta.setAttribute("readonly", "");
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, text.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch (e) {
    console.error("[copyText] execCommand fallback failed:", e);
    return false;
  }
}
