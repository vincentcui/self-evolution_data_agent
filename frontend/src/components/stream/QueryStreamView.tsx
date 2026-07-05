/* ════════════════════════════════════════════
 *  QueryStreamView — Agent SSE 主容器
 * ----------------------------------------------------------------------------
 *  timeline 时序渲染: thinking 和 tool 按 LLM 返回顺序交错展示.
 *  status bar + 操作按钮吸顶, 不随内容滚走.
 * ════════════════════════════════════════════ */

import React, { useRef, useEffect } from "react";
import { Alert, Button, Tag, Space } from "antd";
import { ToolNode } from "./ToolNode";
import { ThinkingBlock } from "./ThinkingBlock";
import { KnowledgeProposedToast } from "./KnowledgeProposedToast";
import { ClarifyCard } from "./ClarifyCard";
import { FinalResult } from "./FinalResult";
import { CorrectionControls } from "./CorrectionControls";
import type { AgentStreamState, TimelineItem } from "@/hooks/useAgentStream";
import type { CorrectionAction } from "@/api/correction";

const STATUS_LABEL: Record<AgentStreamState["status"], string> = {
  idle: "idle",
  running: "running",
  finished: "finished",
  cancelled: "cancelled",
  error: "error",
};

const STATUS_COLOR: Record<AgentStreamState["status"], string> = {
  idle: "default",
  running: "blue",
  finished: "green",
  cancelled: "orange",
  error: "red",
};

const STATUS_HINT: Record<AgentStreamState["status"], string> = {
  idle: "输入问题并选择命名空间后开始",
  running: "任务执行中…",
  finished: "任务已完成",
  cancelled: "任务已取消",
  error: "任务执行出错",
};

interface Props {
  state: AgentStreamState;
  onStop: () => void;
  onClarifyAnswer: (pendingId: number, answer: string) => void;
  onCorrect: (action: CorrectionAction, instruction: string) => void;
  /** 只读历史轮: 隐藏操作按钮, 不吸顶、不内部滚动 (随父容器自然流式堆叠). */
  readOnly?: boolean;
}

/** 渲染单个 timeline item */
const TimelineEntry: React.FC<{ item: TimelineItem; state: AgentStreamState }> = ({
  item,
  state,
}) => {
  switch (item.type) {
    case "thinking":
      return <ThinkingBlock text={item.text} />;
    case "tool": {
      const tool = state.tools.find((t) => t.toolCallId === item.toolCallId);
      if (!tool) return null;
      return <ToolNode node={tool} />;
    }
    case "cost_warning": {
      const w = state.costWarnings[item.index];
      if (!w) return null;
      return (
        <Alert
          type="warning"
          showIcon
          message={`预估扫描 ${w.estimatedDocs.toLocaleString()} 文档 (阈值 ${w.threshold.toLocaleString()})`}
          description={w.advice}
          style={{ marginBottom: 4 }}
        />
      );
    }
    case "warning": {
      const msg = state.warnings[item.index];
      if (!msg) return null;
      return <div style={{ color: "#faad14" }}>⚠️ {msg}</div>;
    }
    case "error": {
      const msg = state.errors[item.index];
      if (!msg) return null;
      return <div style={{ color: "#ff4d4f" }}>❌ {msg}</div>;
    }
    case "knowledge_proposed": {
      const kp = state.knowledgeProposed[item.index];
      if (!kp) return null;
      return <KnowledgeProposedToast items={[kp]} />;
    }
    default:
      return null;
  }
};

export const QueryStreamView: React.FC<Props> = ({
  state,
  onStop,
  onClarifyAnswer,
  onCorrect,
  readOnly = false,
}) => {
  const bottomRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom when new timeline items arrive (仅活跃轮)
  useEffect(() => {
    if (readOnly) return;
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [state.timeline.length, readOnly]);

  // 只读历史轮: 自然流式 (无固定高度 / 无内部滚动); 活跃轮: 占满 + 内部滚动
  const outerStyle: React.CSSProperties = readOnly
    ? { display: "flex", flexDirection: "column", borderBottom: "1px solid #f0f0f0", paddingBottom: 8 }
    : { display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" };
  const contentStyle: React.CSSProperties = readOnly
    ? { padding: "8px 0", display: "flex", flexDirection: "column", gap: 6 }
    : { flex: 1, overflowY: "auto", padding: "8px 0", display: "flex", flexDirection: "column", gap: 6 };

  return (
    <div style={outerStyle}>
      {/* ── header: status + action buttons (历史轮不吸顶, 不显示操作按钮) ── */}
      <div
        style={{
          position: readOnly ? "static" : "sticky",
          top: 0,
          zIndex: 10,
          background: "#fff",
          padding: "8px 0",
          borderBottom: "1px solid #f0f0f0",
          flexShrink: 0,
        }}
      >
        <Space>
          <Tag color={STATUS_COLOR[state.status]}>{STATUS_LABEL[state.status]}</Tag>
          {state.traceId && <Tag>trace: {state.traceId.slice(0, 8)}</Tag>}
          {!readOnly && state.status === "running" && (
            <>
              <Button size="small" danger onClick={onStop}>
                cancel
              </Button>
              <CorrectionControls disabled={false} onCorrect={onCorrect} />
            </>
          )}
        </Space>
      </div>

      {/* ── timeline content ── */}
      <div style={contentStyle}>
        {state.question && (
          <div
            style={{
              background: "#e6f4ff",
              borderRadius: 8,
              padding: "10px 14px",
              marginBottom: 4,
              alignSelf: "flex-end",
              maxWidth: "80%",
              wordBreak: "break-word",
              whiteSpace: "pre-wrap",
            }}
          >
            {state.question}
          </div>
        )}
        {state.timeline.map((item, i) => (
          <TimelineEntry key={i} item={item} state={state} />
        ))}
        {state.pendingClarify && (
          <ClarifyCard pending={state.pendingClarify} onSubmit={onClarifyAnswer} />
        )}
        {state.finalAnswer && (
          <FinalResult
            {...state.finalAnswer}
            stopReason={state.stopReason}
          />
        )}
        {!state.finalAnswer && state.status === "finished" && state.stopReason && state.stopReason !== "end_turn" && (
          <FinalResult content="" stopReason={state.stopReason} />
        )}
        {state.status !== "idle" && (
          <div
            style={{
              color: "rgba(0,0,0,0.45)",
              fontSize: 12,
              textAlign: "center",
              paddingTop: 8,
            }}
          >
            {STATUS_HINT[state.status]}
          </div>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
};

export default QueryStreamView;
