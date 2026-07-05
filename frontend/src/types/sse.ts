/* ============================================================================
 * SSE Agent Event Discriminated Union
 * ----------------------------------------------------------------------------
 * 14 SSE events, 1:1 aligned with backend contracts:
 *   - app/engine/agent_loop.py
 *   - app/engine/sse_manager.py
 *   - app/api/query.py
 * Reference: docs/todos/knowledge-unification-and-agent-loop/02-agent-loop-design.md
 * ========================================================================== */

export type AgentSSEEvent =
  | { event: "agent_started"; data: { trace_id: string; started_at: string } }
  | {
      event: "agent_finished";
      data: {
        stop_reason:
          | "end_turn"
          | "max_exploratory_calls"
          | "max_decisive_calls"
          | "max_total_iterations"
          | "dead_loop"
          | "forced_clarify_timeout"
          | "forced_clarify_exhausted"
          | "llm_timeout";
        total_iterations: number;
        ended_at?: string;
        total_tool_calls?: number;
      };
    }
  | { event: "text_delta"; data: { delta: string } }
  | {
      event: "tool_use";
      data: { tool_call_id: string; name: string; input: Record<string, unknown> };
    }
  | {
      event: "tool_result";
      data: { tool_call_id: string; name?: string; status: "ok" | "error"; output: string };
    }
  | {
      event: "clarify_request";
      data: { pending_id: number; question: string; options?: string[]; reason?: string };
    }
  | { event: "clarify_resolved"; data: { pending_id: number; answer: string } }
  | {
      event: "knowledge_proposed";
      data: { entry_id: number; entry_type: string; preview: string };
    }
  | {
      event: "cost_warning";
      data: { estimated_docs: number; threshold: number; advice?: string };
    }
  | { event: "cancelled"; data: Record<string, never> }
  | { event: "warning"; data: { message: string } }
  | { event: "error"; data: { code: string; message: string; recoverable: boolean } }
  | {
      event: "plan_step_done";
      data: { step_id: number; db_type: string; target: string; row_count: number; exports: string[] };
    }
  | {
      event: "final_answer";
      data: {
        content: string;
        history_id?: number;
        rows?: unknown[];
        columns?: string[];
        chart_type?: string;
        chart_option?: Record<string, unknown>;
        category_column?: string;
        truncated?: boolean;
        rendered_row_count?: number;
        total_row_count?: number;
      };
    }
  | { event: "recommended_questions"; data: { questions: string[] } };

export type EventName = AgentSSEEvent["event"];

/* ---------------------------------------------------------------------------
 * Type guards — narrow union to a single variant for safe field access.
 * ------------------------------------------------------------------------- */

export const isToolUse = (
  e: AgentSSEEvent,
): e is Extract<AgentSSEEvent, { event: "tool_use" }> => e.event === "tool_use";

export const isToolResult = (
  e: AgentSSEEvent,
): e is Extract<AgentSSEEvent, { event: "tool_result" }> => e.event === "tool_result";

export const isFinalAnswer = (
  e: AgentSSEEvent,
): e is Extract<AgentSSEEvent, { event: "final_answer" }> => e.event === "final_answer";

export const isClarify = (
  e: AgentSSEEvent,
): e is Extract<AgentSSEEvent, { event: "clarify_request" }> => e.event === "clarify_request";
