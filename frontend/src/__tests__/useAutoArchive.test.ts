/* ════════════════════════════════════════════
 *  useAutoArchive 单元测试
 * 覆盖 3 条路径:
 *   1. finished + finalAnswer → 触发归档
 *   2. cancelled → 立即归档
 *   3. 重复 status 不重复归档 (archivedRef 守卫)
 * ════════════════════════════════════════════ */

import { describe, it, expect, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { useRef } from "react";
import { useAutoArchive } from "@/hooks/useAutoArchive";
import type { AgentStreamState } from "@/hooks/useAgentStream";

/** 最小可用 AgentStreamState, 各 case 按需覆盖字段 */
function makeState(overrides: Partial<AgentStreamState> = {}): AgentStreamState {
  return {
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
    ...overrides,
  };
}

/** 构造 renderHook 所需的 ref 和 mock, 返回 renderHook 结果 + spied 对象 */
function setup(initialState: AgentStreamState) {
  const stateRef = { current: initialState };
  const turnsRef = { current: [] as AgentStreamState[] };
  const turnsBySession = { current: {} as Record<string, AgentStreamState[]> };
  const runningSessionRef = { current: "s1" as string | null };
  const setTurns = vi.fn();
  const resetAgent = vi.fn();

  const { rerender } = renderHook(
    ({ state }) =>
      useAutoArchive(
        state,
        stateRef,
        turnsRef,
        turnsBySession,
        runningSessionRef,
        setTurns,
        resetAgent,
      ),
    { initialProps: { state: initialState } },
  );

  return { stateRef, turnsRef, turnsBySession, runningSessionRef, setTurns, resetAgent, rerender };
}

// ─────────────────────────────────────────────
//  Case 1: finished + finalAnswer → 触发归档
// ─────────────────────────────────────────────
describe("useAutoArchive", () => {
  it("finished + finalAnswer 触发归档并 reset", () => {
    const finishedState = makeState({
      status: "finished",
      finalAnswer: { content: "答案是 42" },
    });
    const { setTurns, resetAgent, runningSessionRef, turnsBySession } = setup(finishedState);

    expect(setTurns).toHaveBeenCalledTimes(1);
    expect(resetAgent).toHaveBeenCalledTimes(1);
    expect(runningSessionRef.current).toBeNull();
    // 验证 turnsBySession 写入了 "s1"
    expect(turnsBySession.current["s1"]).toBeDefined();
  });

  // ─────────────────────────────────────────────
  //  Case 2: cancelled → 立即归档
  // ─────────────────────────────────────────────
  it("cancelled 立即归档 (无需 finalAnswer)", () => {
    const cancelledState = makeState({ status: "cancelled" });
    const { setTurns, resetAgent, runningSessionRef } = setup(cancelledState);

    expect(setTurns).toHaveBeenCalledTimes(1);
    expect(resetAgent).toHaveBeenCalledTimes(1);
    expect(runningSessionRef.current).toBeNull();
  });

  it("error 立即归档 (无需 finalAnswer)", () => {
    const errorState = makeState({ status: "error" });
    const { setTurns, resetAgent } = setup(errorState);

    expect(setTurns).toHaveBeenCalledTimes(1);
    expect(resetAgent).toHaveBeenCalledTimes(1);
  });

  // ─────────────────────────────────────────────
  //  Case 3: 重复 status 不重复归档
  // ─────────────────────────────────────────────
  it("相同 status 不重复归档 (archivedRef 守卫)", () => {
    const cancelledState = makeState({ status: "cancelled" });
    const { setTurns, resetAgent, rerender } = setup(cancelledState);

    expect(setTurns).toHaveBeenCalledTimes(1);
    expect(resetAgent).toHaveBeenCalledTimes(1);

    // 再次渲染相同 status
    rerender({ state: { ...cancelledState } });
    // 不应再次调用
    expect(setTurns).toHaveBeenCalledTimes(1);
    expect(resetAgent).toHaveBeenCalledTimes(1);
  });

  // ─────────────────────────────────────────────
  //  Case 4: finished 但无 finalAnswer → 不归档
  // ─────────────────────────────────────────────
  it("finished 但无 finalAnswer 不归档 (等待最终答案)", () => {
    const noAnswerState = makeState({ status: "finished", finalAnswer: null });
    const { setTurns, resetAgent } = setup(noAnswerState);

    expect(setTurns).not.toHaveBeenCalled();
    expect(resetAgent).not.toHaveBeenCalled();
  });

  // ─────────────────────────────────────────────
  //  Case 5: running / idle → 不归档
  // ─────────────────────────────────────────────
  it("running 状态不触发归档", () => {
    const runningState = makeState({ status: "running" });
    const { setTurns, resetAgent } = setup(runningState);

    expect(setTurns).not.toHaveBeenCalled();
    expect(resetAgent).not.toHaveBeenCalled();
  });
});
