import { useCallback, useReducer, useRef } from "react";
import { openAgentStream } from "@/api/sseClient";
import type { AgentSSEEvent } from "@/types/sse";

/* ============================================================================
 * useAgentStream — agent SSE 事件驱动 reducer + start/stop hook
 * ----------------------------------------------------------------------------
 * 13 类 SSE 事件归并到 AgentStreamState 单一真相源, 组件订阅 state 即可渲染.
 * AbortController 兜底取消, 串流中断时调用 stop() 立即释放后端 worker.
 * ========================================================================== */

export interface ToolNode {
  toolCallId: string;
  name: string;
  input: Record<string, unknown>;
  output?: string;
  status: "pending" | "ok" | "error" | "cancelled";
}

export interface PendingClarify {
  pendingId: number;
  question: string;
  options?: string[];
  reason?: string;
}

export type TimelineItem =
  | { type: "thinking"; text: string }
  | { type: "tool"; toolCallId: string }
  | { type: "cost_warning"; index: number }
  | { type: "warning"; index: number }
  | { type: "error"; index: number }
  | { type: "knowledge_proposed"; index: number };

export interface AgentStreamState {
  traceId: string | null;
  status: "idle" | "running" | "finished" | "cancelled" | "error";
  stopReason: string | null;
  question: string | null;
  thinking: string;
  tools: ToolNode[];
  timeline: TimelineItem[];
  pendingClarify: PendingClarify | null;
  knowledgeProposed: { entryId: number; entryType: string; preview: string }[];
  costWarnings: { estimatedDocs: number; threshold: number; advice?: string }[];
  warnings: string[];
  errors: string[];
  planSteps: { step_id: number; db_type: string; target: string; row_count: number; exports: string[] }[];
  finalAnswer: {
    content: string;
    historyId?: number;
    rows?: unknown[];
    columns?: string[];
    chartType?: string;
    chartOption?: Record<string, unknown>;
    categoryColumn?: string;
    truncated?: boolean;
    renderedRowCount?: number;
    totalRowCount?: number;
  } | null;
  recommendedQuestions: string[];
}

export const initialAgentStreamState = (): AgentStreamState => ({
  traceId: null,
  status: "idle",
  stopReason: null,
  question: null,
  thinking: "",
  tools: [],
  timeline: [],
  pendingClarify: null,
  knowledgeProposed: [],
  costWarnings: [],
  warnings: [],
  errors: [],
  planSteps: [],
  finalAnswer: null,
  recommendedQuestions: [],
});

type Action =
  | { type: "event"; event: AgentSSEEvent }
  | { type: "reset" }
  | { type: "set_trace"; traceId: string }
  | { type: "set_question"; question: string };

// ---------------------------------------------------------------------------
// agentStreamReducer: 纯函数, 13 case 一一对齐 AgentSSEEvent discriminated union.
// ---------------------------------------------------------------------------
export function agentStreamReducer(state: AgentStreamState, action: Action): AgentStreamState {
  if (action.type === "reset") return initialAgentStreamState();
  if (action.type === "set_trace") return { ...state, traceId: action.traceId };
  if (action.type === "set_question") return { ...state, question: action.question, status: "running" };
  const ev = action.event;
  switch (ev.event) {
    case "agent_started":
      return { ...state, status: "running", traceId: ev.data.trace_id };
    case "agent_finished":
      return { ...state, status: "finished", stopReason: ev.data.stop_reason };
    case "text_delta": {
      const last = state.timeline[state.timeline.length - 1];
      let newTimeline: TimelineItem[];
      if (last && last.type === "thinking") {
        // Append to existing thinking block
        newTimeline = [
          ...state.timeline.slice(0, -1),
          { type: "thinking", text: last.text + ev.data.delta },
        ];
      } else {
        // Start new thinking block
        newTimeline = [...state.timeline, { type: "thinking", text: ev.data.delta }];
      }
      return { ...state, thinking: state.thinking + ev.data.delta, timeline: newTimeline };
    }
    case "tool_use":
      return {
        ...state,
        tools: [
          ...state.tools,
          {
            toolCallId: ev.data.tool_call_id,
            name: ev.data.name,
            input: ev.data.input,
            status: "pending",
          },
        ],
        timeline: [...state.timeline, { type: "tool", toolCallId: ev.data.tool_call_id }],
      };
    case "tool_result":
      return {
        ...state,
        tools: state.tools.map((t) =>
          t.toolCallId === ev.data.tool_call_id
            ? { ...t, status: ev.data.status, output: ev.data.output }
            : t,
        ),
      };
    case "clarify_request":
      return {
        ...state,
        pendingClarify: {
          pendingId: ev.data.pending_id,
          question: ev.data.question,
          options: ev.data.options,
          reason: ev.data.reason,
        },
      };
    case "clarify_resolved":
      return { ...state, pendingClarify: null };
    case "plan_step_done":
      return {
        ...state,
        planSteps: [...state.planSteps, {
          step_id: ev.data.step_id,
          db_type: ev.data.db_type,
          target: ev.data.target,
          row_count: ev.data.row_count,
          exports: ev.data.exports || [],
        }],
      };
    case "knowledge_proposed":
      return {
        ...state,
        knowledgeProposed: [
          ...state.knowledgeProposed,
          {
            entryId: ev.data.entry_id,
            entryType: ev.data.entry_type,
            preview: ev.data.preview,
          },
        ],
        timeline: [
          ...state.timeline,
          { type: "knowledge_proposed", index: state.knowledgeProposed.length },
        ],
      };
    case "cost_warning":
      return {
        ...state,
        costWarnings: [
          ...state.costWarnings,
          {
            estimatedDocs: ev.data.estimated_docs,
            threshold: ev.data.threshold,
            advice: ev.data.advice,
          },
        ],
        timeline: [
          ...state.timeline,
          { type: "cost_warning", index: state.costWarnings.length },
        ],
      };
    case "cancelled":
      return {
        ...state,
        status: "cancelled",
        tools: state.tools.map((t) =>
          t.status === "pending" ? { ...t, status: "cancelled" } : t,
        ),
      };
    case "warning":
      return {
        ...state,
        warnings: [...state.warnings, ev.data.message],
        timeline: [...state.timeline, { type: "warning", index: state.warnings.length }],
      };
    case "error":
      return {
        ...state,
        status: "error",
        errors: [...state.errors, ev.data.message],
        tools: state.tools.map((t) =>
          t.status === "pending" ? { ...t, status: "error" } : t,
        ),
        timeline: [...state.timeline, { type: "error", index: state.errors.length }],
      };
    case "final_answer":
      return {
        ...state,
        finalAnswer: {
          content: ev.data.content,
          historyId: ev.data.history_id,
          rows: ev.data.rows,
          columns: ev.data.columns,
          chartType: ev.data.chart_type,
          chartOption: ev.data.chart_option,
          categoryColumn: ev.data.category_column,
          truncated: ev.data.truncated,
          renderedRowCount: ev.data.rendered_row_count,
          totalRowCount: ev.data.total_row_count,
        },
      };
    case "recommended_questions":
      return {
        ...state,
        recommendedQuestions: ev.data.questions,
      };
    default:
      return state;
  }
}

// ---------------------------------------------------------------------------
// useAgentStream: start() 触发 SSE 长连接, stop() abort 信号传至 fetch reader.
// ---------------------------------------------------------------------------
export function useAgentStream() {
  const [state, dispatch] = useReducer(agentStreamReducer, undefined, initialAgentStreamState);
  const abortRef = useRef<AbortController | null>(null);

  const start = useCallback(
    async (body: { namespace_id: number; question: string; session_id?: string }) => {
      dispatch({ type: "reset" });
      dispatch({ type: "set_question", question: body.question });
      abortRef.current?.abort();
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      try {
        const { traceId, done } = await openAgentStream({
          url: "/api/query/stream",
          body,
          onEvent: (ev) => dispatch({ type: "event", event: ev }),
          signal: ctrl.signal,
        });
        dispatch({ type: "set_trace", traceId });
        await done;
      } catch (err) {
        if ((err as Error).name !== "AbortError") {
          dispatch({
            type: "event",
            event: {
              event: "error",
              data: {
                code: "stream_error",
                message: (err as Error).message,
                recoverable: false,
              },
            },
          });
        }
      }
    },
    [],
  );

  const stop = useCallback(() => abortRef.current?.abort(), []);

  const reset = useCallback(() => dispatch({ type: "reset" }), []);

  return { state, start, stop, reset };
}
