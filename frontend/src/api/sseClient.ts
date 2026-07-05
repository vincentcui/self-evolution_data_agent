import type { AgentSSEEvent } from "@/types/sse";

// ============================================================================
// SSE client for POST /api/query/stream
// ----------------------------------------------------------------------------
// 后端 stream 端点用 POST + body 传 namespace_id/question/session_id, 原生
// EventSource 仅支持 GET, 故走 fetch + ReadableStream 自解析 SSE 协议.
// ============================================================================

export interface OpenStreamOpts {
  url: string;
  body: Record<string, unknown>;
  onEvent: (ev: AgentSSEEvent) => void;
  onError?: (err: Error) => void;
  signal?: AbortSignal;
}

export interface StreamHandle {
  traceId: string;
  done: Promise<void>;
}

// ---------------------------------------------------------------------------
// parseSSEChunk: 累积式增量解析, 以 \n\n 切分事件块, tail 作为下一轮 leftover.
// ---------------------------------------------------------------------------
export function parseSSEChunk(
  chunk: string,
  leftover: string,
): { events: AgentSSEEvent[]; leftover: string } {
  const buf = leftover + chunk;
  const parts = buf.split("\n\n");
  const tail = parts.pop() ?? "";
  const events: AgentSSEEvent[] = [];
  for (const block of parts) {
    if (!block.trim() || block.startsWith(":")) continue;
    let evName: string | null = null;
    let dataStr = "";
    for (const line of block.split("\n")) {
      if (line.startsWith("event:")) evName = line.slice(6).trim();
      else if (line.startsWith("data:")) dataStr += line.slice(5).trim();
    }
    if (!evName) continue;
    try {
      events.push({ event: evName, data: JSON.parse(dataStr || "{}") } as AgentSSEEvent);
    } catch {
      /* malformed event 跳过, 不阻流 */
    }
  }
  return { events, leftover: tail };
}

// ---------------------------------------------------------------------------
// openAgentStream: fetch POST, 拿 X-Trace-Id 头, reader 循环 decode 喂 parser.
// ---------------------------------------------------------------------------
export async function openAgentStream(opts: OpenStreamOpts): Promise<StreamHandle> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };
  const token = localStorage.getItem("token");
  if (token) headers.Authorization = `Bearer ${token}`;
  const resp = await fetch(opts.url, {
    method: "POST",
    headers,
    body: JSON.stringify(opts.body),
    signal: opts.signal,
    credentials: "include",
  });
  if (!resp.ok || !resp.body) throw new Error(`SSE stream failed: HTTP ${resp.status}`);
  const traceId = resp.headers.get("X-Trace-Id") ?? "";
  const reader = resp.body.getReader();
  // signal abort → cancel reader（仅 abort fetch 不够，reader 在连接建立后不受 signal 控制）
  if (opts.signal) {
    opts.signal.addEventListener("abort", () => reader.cancel(), { once: true });
  }
  const decoder = new TextDecoder();
  let leftover = "";
  const done = (async () => {
    try {
      while (true) {
        const { value, done: rd } = await reader.read();
        if (rd) break;
        const { events, leftover: rest } = parseSSEChunk(
          decoder.decode(value, { stream: true }),
          leftover,
        );
        leftover = rest;
        for (const ev of events) opts.onEvent(ev);
      }
    } catch (err) {
      opts.onError?.(err instanceof Error ? err : new Error(String(err)));
      throw err;
    }
  })();
  return { traceId, done };
}
