/* ════════════════════════════════════════════
 *  useAutoArchive — 对话结束时自动归档到 turns 列表
 * ----------------------------------------------------------------------------
 *  监听 AgentStreamState.status + finalAnswer，在 finished/cancelled/error
 *  时自动将当前回合移入历史区并 reset live state。
 *
 *  ⚠️ agent_finished SSE 先于 final_answer 到达，finished 状态需等待
 *     finalAnswer 就绪后再归档（否则归档的 state 不含最终答案）。
 * ════════════════════════════════════════════ */

import { useEffect, useRef } from "react";
import type { AgentStreamState } from "./useAgentStream";

export function useAutoArchive(
  state: AgentStreamState,
  stateRef: React.MutableRefObject<AgentStreamState>,
  turnsRef: React.MutableRefObject<AgentStreamState[]>,
  turnsBySession: React.MutableRefObject<Record<string, AgentStreamState[]>>,
  runningSessionRef: React.MutableRefObject<string | null>,
  setTurns: React.Dispatch<React.SetStateAction<AgentStreamState[]>>,
  resetAgent: () => void,
) {
  const archivedRef = useRef(false);

  useEffect(() => {
    const doArchive = () => {
      if (archivedRef.current) return;
      archivedRef.current = true;
      const latestState = stateRef.current;
      const ownerSid = runningSessionRef.current;
      setTurns((prevTurns) => [...prevTurns, latestState]);
      if (ownerSid) {
        turnsBySession.current[ownerSid] = [...turnsRef.current, latestState];
      }
      resetAgent();
      runningSessionRef.current = null;
    };

    // cancelled / error：立即归档（可能没有 finalAnswer）
    if (state.status === "cancelled" || state.status === "error") {
      doArchive();
      return;
    }
    // finished：等 finalAnswer 就绪后归档
    if (state.status === "finished" && state.finalAnswer) {
      doArchive();
    }
  }, [state.status, state.finalAnswer]);

  return archivedRef;
}
