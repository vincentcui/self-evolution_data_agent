/* ════════════════════════════════════════════
 *  智能查询页 — 主页 (Stage 6: SSE Agent Loop)
 *  useAgentStream + QueryStreamView 单一路径
 *
 *  布局:
 *    - idle: logo + 输入框居中 (Kimi 风格首屏)
 *    - 对话中: 顶部栏 / 中间可滚动区 / 底部固定输入框 (Kimi 对话页风格)
 *              自动滚动到底部 — 用户手动上滚时暂停跟随, 滚回底部恢复跟随
 * ════════════════════════════════════════════ */

import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Alert } from "antd";
import NamespaceSelector from "@/components/NamespaceSelector";
import ChatInput from "@/components/ChatInput";
import { QueryStreamView } from "@/components/stream/QueryStreamView";
import { http } from "@/api";
import { useAgentStream, initialAgentStreamState, type AgentStreamState } from "@/hooks/useAgentStream";
import { useReadiness } from "@/hooks/useReadiness";
import { useSessionContext } from "@/context/SessionContext";
import { useAuth } from "@/context/AuthContext";
import { roleAtLeast } from "@/utils/role";
import { submitCorrection, submitClarifyResponse, cancelStream } from "@/api/correction";
import type { CorrectionAction } from "@/api/correction";
import styles from "@/styles/query.module.css";

/** 距底阈值 — 小于这个距离就认为用户"还在底部", 可自动跟随. */
const FOLLOW_THRESHOLD_PX = 64;

const QueryPage: React.FC = () => {
  const { user } = useAuth();
  const isAdmin = roleAtLeast(user?.role, "admin");
  const [nsId, setNsId] = useState<number>();
  const { ready, blockers } = useReadiness(nsId ?? null);
  const { activeSessionId, sessions, renameSession } = useSessionContext();
  const { state, start, stop } = useAgentStream();
  // 已归档的历史轮次 (已完成/已取消) — 新一轮开始前把当前轮快照推入, 防被 reset 清空
  const [turns, setTurns] = useState<AgentStreamState[]>([]);
  // 会话切换时加载历史: 按 Q&A 对分组, 只渲染 assistant 回答
  useEffect(() => {
    if (!activeSessionId || !nsId) return;
    http.get(`/namespaces/${nsId}/history`, { params: { session_id: activeSessionId, limit: 100 } })
      .then((r) => {
        const histories: any[] = r.data;
        if (histories.length === 0) return;
        const pairedTurns: AgentStreamState[] = [];
        for (let i = 0; i < histories.length; i++) {
          if (histories[i].role !== "assistant") continue;
          const question = i > 0 && histories[i - 1].role === "user"
            ? histories[i - 1].content : "";
          pairedTurns.push({
            ...initialAgentStreamState(),
            status: "finished" as const,
            finalAnswer: { content: histories[i].content, historyId: histories[i].id },
            question,
          });
        }
        if (pairedTurns.length > 0) setTurns(pairedTurns);
      })
      .catch(() => { /* 静默失败 */ });
  }, [activeSessionId, nsId]);

  // ── 有礼貌的自动跟随 ──────────────────────────────────
  const scrollRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);   // 用户是否仍在底部, 决定是否自动拉到底

  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    followRef.current = distanceFromBottom < FOLLOW_THRESHOLD_PX;
  };

  // state 变化触发 (thinking / tools / final_answer / status) — 若在跟随则拉到底
  useEffect(() => {
    if (!followRef.current) return;
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [state]);

  const handleSend = async (question: string) => {
    if (!nsId) return;
    followRef.current = true; // 新一轮发问, 强制回到底部跟随
    // 归档当前轮 (非 idle) 为只读历史, 防 start() 内部 reset 清空上一轮会话
    if (state.status !== "idle") {
      setTurns((prev) => [...prev, state]);
    }
    const sid = activeSessionId ?? "";
    await start({ namespace_id: nsId, question, session_id: sid });
    // 首次提问后自动更新会话标题
    const activeSession = sessions.find((s) => s.id === activeSessionId);
    if (activeSession && activeSession.title === "新会话") {
      try {
        await renameSession(activeSession.id, question.slice(0, 30));
      } catch { /* 静默失败 */ }
    }
  };

  const handleStop = async () => {
    if (state.traceId) {
      try { await cancelStream(state.traceId); } catch { /* ignore */ }
    }
    stop();
  };

  const handleClarifyAnswer = async (pendingId: number, answer: string) => {
    if (!state.traceId) return;
    await submitClarifyResponse(state.traceId, { pending_id: pendingId, answer });
  };

  const handleCorrect = async (action: CorrectionAction, instruction: string) => {
    if (!state.traceId) return;
    if (action !== "abort" && !instruction) return;
    await submitCorrection(state.traceId, { action, instruction });
  };

  const running = state.status === "running";
  const inputDisabled = running || !nsId || (nsId != null && !ready);
  const isIdle = state.status === "idle" && turns.length === 0;

  const blockerAlert = nsId != null && blockers.length > 0 && (
    <Alert
      type="warning"
      showIcon
      style={{ marginTop: 12, maxWidth: 600, textAlign: "left" }}
      message={blockers[0].message}
      description={
        isAdmin && blockers[0].admin_route
          ? <Link to={blockers[0].admin_route}>{blockers[0].admin_action}</Link>
          : blockers[0].user_action
      }
    />
  );

  if (isIdle) {
    return (
      <div className={`${styles.pageContainer} ${styles.pageIdle}`}>
        <NamespaceSelector
          value={nsId}
          onChange={(id) => setNsId(id)}
        />
        <div className={styles.idleWrapper}>
          <div className={styles.logo}>NL2QL</div>
          <ChatInput onSend={handleSend} loading={inputDisabled} />
          {blockerAlert}
        </div>
      </div>
    );
  }

  return (
    <div className={styles.pageContainer}>
      <div className={styles.chatHeader}>
        <NamespaceSelector
          value={nsId}
          onChange={(id) => setNsId(id)}
        />
      </div>
      <div
        className={styles.chatScroll}
        ref={scrollRef}
        onScroll={handleScroll}
      >
        {/* 历史轮次 — 只读, 不渲染操作按钮 */}
        {turns.map((turn, i) => (
          <QueryStreamView
            key={turn.traceId ?? `turn-${i}`}
            state={turn}
            readOnly
            onStop={() => {}}
            onClarifyAnswer={() => {}}
            onCorrect={() => {}}
          />
        ))}
        {/* 当前活跃轮 — 完整交互 (idle 时不渲染, 避免历史后多一个空块) */}
        {state.status !== "idle" && (
          <QueryStreamView
            state={state}
            onStop={handleStop}
            onClarifyAnswer={handleClarifyAnswer}
            onCorrect={handleCorrect}
            onSendQuestion={handleSend}
          />
        )}
      </div>
      <div className={styles.chatFooter}>
        <ChatInput onSend={handleSend} loading={inputDisabled} />
        {blockerAlert}
      </div>
    </div>
  );
};

export default QueryPage;
