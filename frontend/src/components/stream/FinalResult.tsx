/* ════════════════════════════════════════════
 *  FinalResult — 最终结果卡片 (内嵌 ResultDisplay + 分享按钮)
 * ----------------------------------------------------------------------------
 *  对外 props 由 plan 定义 (content/rows/columns/chartType/historyId).
 *  ResultDisplay 实际签名是 `result: QueryResponse`, 内部把 rows 归一化成
 *  二维数组并 fabricate 一个最小 QueryResponse 投喂下去.
 * ════════════════════════════════════════════ */

import React, { useState } from "react";
import { Card, Button, message, Input, Space, Popover, Alert } from "antd";
import {
  ShareAltOutlined,
  CopyOutlined,
  LikeOutlined,
  DislikeOutlined,
} from "@ant-design/icons";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import ResultDisplay from "@/components/ResultDisplay";
import { createShare, submitFeedback } from "@/api";
import type { QueryResponse } from "@/types";
import mdStyles from "@/styles/markdown.module.css";

interface Props {
  content: string;
  rows?: unknown[];
  columns?: string[];
  chartType?: string;
  chartOption?: Record<string, unknown>;
  categoryColumn?: string;
  valueColumn?: string;
  seriesBy?: string;
  truncated?: boolean;
  renderedRowCount?: number;
  totalRowCount?: number;
  historyId?: number;
  stopReason?: string | null;
}

import { normalizeRows } from "@/utils/normalizeRows";

const EXPIRY_OPTIONS = [
  { label: "1天", ms: 86400000 },
  { label: "7天", ms: 7 * 86400000 },
  { label: "30天", ms: 30 * 86400000 },
  { label: "永不过期", ms: 0 },
] as const;

export const STOP_REASON_HINT: Record<string, string> = {
  dead_loop:     "检测到连续重复调用，已自动中止。",
  stagnation:    "连续多次工具调用没有有效结果，已自动中止。",
  cost_exhausted: "已达到总轮次成本后墙，当前进展可查看工具调用记录。",
  // 兼容旧后端（新后端已改为 cost_exhausted）
  max_total_iterations: "总轮次已达上限，当前进展可查看工具调用记录。",
  forced_clarify_timeout: "因反复命中同类错误已向你发起澄清，但等待回应超时，已中止本次查询。",
  forced_clarify_exhausted: "已就同类错误多次向你澄清仍未解决，为避免空耗已中止本次查询。",
};

export const FinalResult: React.FC<Props> = ({
  content, rows, columns, chartType, chartOption, categoryColumn, valueColumn, seriesBy,
  truncated, renderedRowCount, totalRowCount, historyId, stopReason,
}) => {
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [sharing, setSharing] = useState(false);
  const [popoverOpen, setPopoverOpen] = useState(false);
  const [feedback, setFeedback] = useState<"like" | "dislike" | null>(null);

  const showResult = (rows && rows.length > 0 && columns && columns.length > 0)
    || (chartType && chartType !== "table" && chartOption && Object.keys(chartOption).length > 0);
  const fabricated: QueryResponse | null = showResult
    ? {
        session_id: "",
        history_id: historyId ?? 0,
        needs_clarification: false,
        clarification_message: "",
        generated_query: "",
        columns: columns ?? [],
        rows: (rows && columns) ? normalizeRows(rows!, columns!) as any[][] : [],
        row_count: rows?.length ?? 0,
        chart_type: (chartType ?? "table") as QueryResponse["chart_type"],
        category_column: categoryColumn ?? "",
        value_column: valueColumn ?? "",
        series_by: seriesBy ?? "",
        chart_option: chartOption ?? {},
        performance_warning: "",
        truncated: truncated ?? false,
        rendered_row_count: renderedRowCount ?? 0,
        total_row_count: totalRowCount ?? 0,
        error: "",
        clarification_questions: [],
        pending_id: 0,
      }
    : null;

  const handleShare = async (ms: number) => {
    if (!historyId) {
      message.warning("无法分享: 缺少查询记录 ID");
      return;
    }
    setPopoverOpen(false);
    setSharing(true);
    try {
      const expiresAt = ms > 0 ? new Date(Date.now() + ms).toISOString() : undefined;
      const resp = await createShare(historyId, expiresAt);
      const token = resp.token || resp.share_token;
      const url = `${window.location.origin}/share/${token}`;
      setShareUrl(url);
      await navigator.clipboard.writeText(url);
      message.success("分享链接已复制到剪贴板");
    } catch (e: any) {
      const msg = e?.response?.data?.detail || "分享失败";
      message.error(msg);
    } finally {
      setSharing(false);
    }
  };

  const handleCopyAnswer = async () => {
    try {
      await navigator.clipboard.writeText(content);
      message.success("回答已复制");
    } catch {
      message.error("复制失败，请重试");
    }
  };

  const handleCopyShareUrl = () => {
    if (shareUrl) {
      navigator.clipboard.writeText(shareUrl);
      message.success("已复制到剪贴板");
    }
  };

  const handleFeedback = async (rating: "like" | "dislike") => {
    if (!historyId) return;
    // 互斥: 点击相同值取消, 点击另一个值切换
    const newRating = feedback === rating ? null : rating;
    try {
      await submitFeedback(historyId, newRating ?? rating);
      setFeedback(newRating);
    } catch {
      message.error("反馈失败，请重试");
    }
  };

  const expiryContent = (
    <Space direction="vertical" size="small">
      {EXPIRY_OPTIONS.map((opt) => (
        <Button
          key={opt.label}
          type="text"
          size="small"
          block
          onClick={() => handleShare(opt.ms)}
        >
          {opt.label}
        </Button>
      ))}
    </Space>
  );

  return (
    <>
    <Card
      title="✅ 最终结果"
      style={{ marginTop: 12 }}
      extra={
        <Space size="small">
          <Button icon={<CopyOutlined />} size="small" onClick={handleCopyAnswer}>
            复制
          </Button>
          {historyId && (
            <>
              <Button
                icon={<LikeOutlined />}
                size="small"
                type={feedback === "like" ? "primary" : "default"}
                onClick={() => handleFeedback("like")}
              />
              <Button
                icon={<DislikeOutlined />}
                size="small"
                type={feedback === "dislike" ? "primary" : "default"}
                onClick={() => handleFeedback("dislike")}
              />
              <Popover
                content={expiryContent}
                title="选择有效期"
                trigger="click"
                open={popoverOpen}
                onOpenChange={setPopoverOpen}
              >
                <Button
                  icon={<ShareAltOutlined />}
                  size="small"
                  loading={sharing}
                >
                  分享
                </Button>
              </Popover>
            </>
          )}
        </Space>
      }
    >
      {shareUrl && (
        <Space style={{ marginBottom: 12, width: "100%" }}>
          <Input value={shareUrl} readOnly style={{ width: 360 }} size="small" />
          <Button icon={<CopyOutlined />} size="small" onClick={handleCopyShareUrl}>
            复制
          </Button>
        </Space>
      )}
      {stopReason && STOP_REASON_HINT[stopReason] && (
        <Alert
          type="warning"
          showIcon
          message={STOP_REASON_HINT[stopReason]}
          style={{ marginBottom: 12 }}
        />
      )}
      {content && (
        <div className={mdStyles.md}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
        </div>
      )}
      {fabricated && <ResultDisplay result={fabricated} />}
    </Card>
    </>
  );
};

export default FinalResult;
