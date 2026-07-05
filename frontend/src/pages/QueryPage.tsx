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
import { Alert, Button, Modal, Spin } from "antd";
import NamespaceSelector from "@/components/NamespaceSelector";
import ChatInput from "@/components/ChatInput";
import { QueryStreamView } from "@/components/stream/QueryStreamView";
import { http } from "@/api";
import { useAgentStream, initialAgentStreamState, type AgentStreamState } from "@/hooks/useAgentStream";
import { useAutoArchive } from "@/hooks/useAutoArchive";
import { useReadiness } from "@/hooks/useReadiness";
import { readLastNamespaceId } from "@/hooks/useLastNamespaceId";
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
  const [nsRefreshKey, setNsRefreshKey] = useState(0);
  const [namespaceCount, setNamespaceCount] = useState(0);
  const refreshNsCount = () => {
    http.get("/namespaces").then((r) => setNamespaceCount(r.data.length)).catch(() => {});
  };
  useEffect(() => { refreshNsCount(); }, []);
  const { activeSessionId, setActiveSessionId, createSession, renameSession, resetKey, setIsRunning, setRunningTraceId, currentNamespaceId, setCurrentNamespaceId, wsOpen, setWsOpen, wsPage, setWsPage, loading: sessionsLoading } = useSessionContext();
  const nsId = currentNamespaceId;
  const setNsId = setCurrentNamespaceId;
  const { ready, blockers, refresh: refreshReadiness } = useReadiness(nsId);
  // 工作台关闭后刷新
  useEffect(() => { if (!wsOpen) { refreshNsCount(); setNsRefreshKey((k) => k + 1); refreshReadiness(); } }, [wsOpen]);
  const { state, start, stop, reset: resetAgent } = useAgentStream();
  // 按 session 缓存轮次：切换会话时恢复，新对话时保留
  const turnsBySession = useRef<Record<string, AgentStreamState[]>>({});
  const [turns, setTurns] = useState<AgentStreamState[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  // 会话切换时加载历史: 按 Q&A 对分组, 只渲染 assistant 回答
  useEffect(() => {
    if (!activeSessionId) return;
    // 若已有非空缓存（含本轮实时累积的完整过程），跳过 API 避免不完整数据覆盖缓存
    const existing = turnsBySession.current[activeSessionId];
    if (existing && existing.length > 0) return;
    const fetchNsId = nsId ?? readLastNamespaceId();
    if (!fetchNsId) return;
    const sidAtRequest = activeSessionId;  // 快照：防止回调时 session 已切换导致数据覆盖
    setHistoryLoading(true);
    http.get(`/namespaces/${fetchNsId}/history`, { params: { session_id: sidAtRequest, limit: 100 } })
      .then((r) => {
        // 回调时 session 可能已切换，丢弃不匹配的响应
        if (sidAtRequest !== activeSessionId) { setHistoryLoading(false); return; }
        const histories: any[] = r.data;
        if (histories.length === 0) {
          setTurns([]);
          turnsBySession.current[sidAtRequest] = [];
          setHistoryLoading(false);
          return;
        }
        const pairedTurns: AgentStreamState[] = [];
        for (let i = 0; i < histories.length; i++) {
          if (histories[i].role !== "assistant") continue;
          let answer = histories[i].content;
          let toolTrace: any[] = [];
          let snap: any = {};
          // 答案存在 result_snapshot.final_answer 中
          try {
            snap = JSON.parse(histories[i].result_snapshot || "{}");
            if (snap.final_answer) answer = snap.final_answer;
            toolTrace = snap.tool_trace || [];
          } catch {}
          const question = histories[i].content || "";
          // 从保存的 tool_trace 重建工具节点和时间线
          const tools: import("@/hooks/useAgentStream").ToolNode[] = [];
          const timeline: import("@/hooks/useAgentStream").TimelineItem[] = [];
          for (const tt of toolTrace) {
            const tcId = tt.id || tt.tool_call_id || `tool-${tools.length}`;
            tools.push({
              toolCallId: tcId,
              name: tt.name || "unknown",
              input: tt.input || {},
              output: typeof tt.output === "string" ? tt.output : JSON.stringify(tt.output || {}, null, 2),
              status: tt.status === "ok" ? "ok" : tt.status === "error" ? "error" : "ok",
            });
            timeline.push({ type: "tool", toolCallId: tcId });
          }
          // 从保存的列数据构建 FinalResult
          const columns: string[] = snap.columns || [];
          const rows: unknown[] = snap.rows || [];
          const stopReason: string = snap.stop_reason || (snap.error === "cancelled" ? "cancelled" : "end_turn");
          pairedTurns.push({
            ...initialAgentStreamState(),
            status: stopReason === "cancelled" ? "cancelled" as const : "finished" as const,
            stopReason,
            finalAnswer: {
              content: answer,
              historyId: histories[i].id,
              rows: rows.length > 0 ? rows : undefined,
              columns: columns.length > 0 ? columns : undefined,
              chartType: snap.chart_type,
              chartOption: snap.chart_option,
              categoryColumn: snap.category_column,
              truncated: snap.truncated,
              renderedRowCount: snap.rendered_row_count,
              totalRowCount: snap.total_row_count,
            },
            question,
            tools,
            timeline,
          });
        }
        setTurns(pairedTurns.length > 0 ? pairedTurns : []);
        // 写入缓存，防止 doSwitch() 在 fetch 早于用户确认完成时误清数据
        turnsBySession.current[sidAtRequest] = pairedTurns.length > 0 ? pairedTurns : [];
        setHistoryLoading(false);
      })
      .catch((err: any) => {
        setHistoryLoading(false);
        const status = err?.response?.status;
        const detail = err?.response?.data?.detail || "";
        if (status === 403) {
          Modal.warning({ title: "无权限", content: "你无权访问该会话所属空间" });
        } else if (status && status !== 404) {
          // 404 后端不区分"ns不存在"和"无session记录"，静默处理
          Modal.warning({ title: "加载失败", content: detail || `请求失败 (${status})，请刷新后重试` });
        }
      });
  }, [activeSessionId, nsId]);

  // 跟踪 traceId 用于取消
  useEffect(() => { if (state.traceId) setRunningTraceId(state.traceId); }, [state.traceId]);

  // 跟踪最新 state + turns，避免闭包陈旧（需在 useAutoArchive 之前定义）
  const stateRef = useRef(state);
  stateRef.current = state;
  const turnsRef = useRef(turns);
  turnsRef.current = turns;

  // 自动归档：对话结束（finished/cancelled/error）时自动移入历史区
  const runningSessionRef = useRef<string | null>(null);
  const archivedRef = useAutoArchive(state, stateRef, turnsRef, turnsBySession, runningSessionRef, setTurns, resetAgent);

  // 跟踪最新 state.status，避免闭包陈旧
  const statusRef = useRef(state.status);
  statusRef.current = state.status;

  // 切换会话：存档当前 → 加载目标
  const prevSidRef = useRef<string | null>(null);
  useEffect(() => {
    const prev = prevSidRef.current;
    const sid = activeSessionId;
    if (prev === sid) return;

    const doSwitch = () => {
      // 用 ref 读取最新值，避免闭包陈旧导致数据覆盖到错误 session
      const latestState = stateRef.current;
      const latestTurns = turnsRef.current;

      // 存档上一个会话
      if (prev) {
        if (latestState.status !== "idle") {
          const finalState = latestState.status === "running"
            ? { ...latestState, status: "cancelled" as const, tools: latestState.tools.map((t: any) => t.status === "pending" ? { ...t, status: "cancelled" } : t) }
            : latestState;
          // 仅当该轮未在 turns 末尾时追加
          const lastTurn = latestTurns[latestTurns.length - 1];
          turnsBySession.current[prev] = (lastTurn && lastTurn.traceId === latestState.traceId)
            ? latestTurns
            : [...latestTurns, finalState];
        } else {
          turnsBySession.current[prev] = latestTurns;
        }
      }
      // 加载目标会话
      if (sid) {
        const cached = turnsBySession.current[sid];
        if (cached && cached.length > 0) {
          setTurns(cached);
        } else {
          setTurns([]);
          setHistoryLoading(true);
        }
      } else {
        setTurns([]);
      }
      resetAgent();
      prevSidRef.current = sid;
    };

    if (prev && sid && statusRef.current === "running") {
      Modal.confirm({
        title: "当前有任务正在执行",
        content: "切换会话将停止当前正在执行的任务，是否继续？",
        okText: "停止并切换", cancelText: "取消",
        onOk: async () => {
          // 先中止客户端 SSE，再等待后端取消完成
          stop();
          if (stateRef.current.traceId) await cancelStream(stateRef.current.traceId).catch(() => {});
          setIsRunning(false); doSwitch();
        },
        onCancel: () => { prevSidRef.current = prev; },
      });
    } else {
      doSwitch();
    }
  }, [activeSessionId]);

  // 新对话：存档当前 → 清空
  const prevResetRef = useRef(resetKey);
  useEffect(() => {
    if (resetKey !== prevResetRef.current) {
      prevResetRef.current = resetKey;
      const latestState = stateRef.current;
      const latestTurns = turnsRef.current;
      // 存档当前会话的完整状态（避免与 auto-archive 重复）
      if (activeSessionId && latestState.status !== "idle") {
        const finalState = latestState.status === "running"
          ? { ...latestState, status: "cancelled" as const, tools: latestState.tools.map((t: any) => t.status === "pending" ? { ...t, status: "cancelled" } : t) }
          : latestState;
        const lastTurn = latestTurns[latestTurns.length - 1];
        turnsBySession.current[activeSessionId] = (lastTurn && lastTurn.traceId === latestState.traceId)
          ? latestTurns
          : [...latestTurns, finalState];
      }
      if (latestState.status === "running") { stop(); setIsRunning(false); }
      setTurns([]);
      resetAgent();
    }
  }, [resetKey]);

  // ── 有礼貌的自动跟随 ──────────────────────────────────
  const scrollRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);

  const handleScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    followRef.current = distanceFromBottom < FOLLOW_THRESHOLD_PX;
  };

  // state 变化触发滚动 — 若在跟随则拉到底；新 SSE 内容到达时自动恢复跟随
  const prevTimelineLenRef = useRef(0);
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    // handleSend 中暂停了跟随（保留旧对话在视口内），新内容到达时恢复
    const newLen = state.timeline.length;
    const hasNewContent = newLen > prevTimelineLenRef.current || (state.finalAnswer != null);
    if (hasNewContent && state.status === "running") {
      followRef.current = true;
    }
    prevTimelineLenRef.current = newLen;
    if (!followRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [state]);

  const handleSend = async (question: string) => {
    if (!nsId) return;
    followRef.current = false;  // 暂停跟随，保留旧对话内容在视口内可见
    // 若上一轮还在运行中（未触发 auto-archive）则手动归档，避免 start() 内部 reset 丢失数据
    const curState = stateRef.current;
    if (curState.status === "running") {
      const cancelledState = { ...curState, status: "cancelled" as const, tools: curState.tools.map((t: any) => t.status === "pending" ? { ...t, status: "cancelled" } : t) };
      setTurns((prev) => [...prev, cancelledState]);
      const ownerSid = runningSessionRef.current || activeSessionId;
      if (ownerSid) {
        turnsBySession.current[ownerSid] = [...turnsRef.current, cancelledState];
      }
      archivedRef.current = true;
      stop();
    }
    // 无活跃会话时自动创建
    let sid = activeSessionId;
    let isNew = false;
    if (!sid && nsId) {
      try {
        const ns = await createSession(nsId);
        sid = ns.id;
        isNew = true;
        setActiveSessionId(sid);
      } catch {
        Modal.error({ title: "创建会话失败", content: "无法创建对话会话，请刷新页面后重试" });
        return;
      }
    }
    // 新会话立即用问题作为标题，不受后续取消影响
    if (isNew && sid) {
      try { await renameSession(sid, question.slice(0, 30)); } catch {}
    }
    setIsRunning(true);
    archivedRef.current = false;  // 新一轮对话，重置归档标志
    runningSessionRef.current = sid;  // 记录本对话归属的 session，防止切换后 auto-archive 写错缓存
    await start({ namespace_id: nsId, question, session_id: sid ?? "" });
    setIsRunning(false);
    setRunningTraceId(null);
    // runningSessionRef 在 auto-archive effect 中清空（effect 异步执行，此处同步置 null 会导致缓存写入跳过）
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
  const isIdle = !activeSessionId && turns.length === 0;

  // 页面初始加载：sessions 尚未加载完毕，显示全屏 loading，避免闪现空白或 idle 页
  if (sessionsLoading && !activeSessionId) {
    return (
      <div className={`${styles.pageContainer} ${styles.pageIdle}`}>
        <NamespaceSelector key={nsRefreshKey} value={nsId ?? undefined} onChange={(id) => setNsId(id ?? null)} />
        <div className={styles.idleWrapper}>
          <Spin size="default" />
          <div style={{ color: "#999", fontSize: 13, marginTop: 12 }}>加载中…</div>
        </div>
      </div>
    );
  }


  const pageByBlocker: Record<string, string> = {
    no_datasource: "namespaces", no_api_key: "model-management", no_schema: "namespaces",
  };
  const openWorkspace = (blockerType: string) => {
    setWsPage(pageByBlocker[blockerType] || "namespaces");
    setWsOpen(true);
  };

  const blockingList: any[] = namespaceCount === 0
    ? [{ type: "no_ns", message: "无可用的命名空间", admin_action: "去创建命名空间", user_action: "请联系管理员配置" }]
    : nsId == null
    ? [{ type: "no_select", message: "请先选择命名空间", admin_action: "", user_action: "" }]
    : blockers;
  const blockerAlert = blockingList.length > 0 && (
    <div style={{ marginTop: 12, maxWidth: 600, textAlign: "left" }}>
      {blockingList.map((b, i) => (
        <Alert
          key={b.type}
          type="warning"
          showIcon
          banner
          style={{ marginBottom: i < blockingList.length - 1 ? 4 : 0 }}
          message={
            <span>
              {b.message}
              {(b.admin_action || b.user_action) && <span style={{ marginLeft: 8 }}>
                {isAdmin && b.admin_action
                  ? <Button type="link" style={{ padding: 0, height: "auto", fontSize: 13 }} onClick={() => openWorkspace(b.type)}>{b.admin_action}</Button>
                  : <span style={{ color: "#666", fontSize: 13 }}>{b.user_action}</span>}
              </span>}
            </span>
          }
        />
      ))}
    </div>
  );

  if (isIdle) {
    return (
      <>
      <div className={`${styles.pageContainer} ${styles.pageIdle}`}>
        <NamespaceSelector key={nsRefreshKey} value={nsId ?? undefined} onChange={(id) => setNsId(id ?? null)} />
        <div className={styles.idleWrapper}>
          <div className={styles.logo}>NL2QL</div>
          <ChatInput onSend={handleSend} loading={inputDisabled} />
          {blockerAlert}
        </div>
      </div>
      </>
    );
  }

  return (
    <>
    <div className={styles.pageContainer}>
      <div className={styles.chatHeader}>
        <NamespaceSelector key={nsRefreshKey} value={nsId ?? undefined} onChange={(id) => setNsId(id ?? null)} />
      </div>
      <div
        className={styles.chatScroll}
        ref={scrollRef}
        onScroll={handleScroll}
      >
        {historyLoading && turns.length === 0 && (
          <div style={{ textAlign: "center", padding: "48px 0" }}>
            <Spin size="default" />
            <div style={{ color: "#999", fontSize: 13, marginTop: 12 }}>加载对话记录…</div>
          </div>
        )}
        {!historyLoading && turns.length === 0 && activeSessionId && state.status === "idle" && (
          <div style={{ textAlign: "center", padding: "48px 0", color: "#bbb", fontSize: 14 }}>
            此会话暂无对话记录
          </div>
        )}
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
        {state.status !== "idle" && activeSessionId && (
          <QueryStreamView
            state={state}
            onStop={handleStop}
            onClarifyAnswer={handleClarifyAnswer}
            onCorrect={handleCorrect}
          />
        )}
      </div>
      <div className={styles.chatFooter}>
        <ChatInput onSend={handleSend} loading={inputDisabled} />
        {blockerAlert}
      </div>
    </div>
    </>
  );
};

export default QueryPage;
