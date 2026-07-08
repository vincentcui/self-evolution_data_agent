/* ════════════════════════════════════════════
 *  HypotheticalQueriesPanel — 展示 + 编辑 HyQE 生成的假设触发问题 (rule / route_hint)
 *
 *  Stage 2 抓手 A: ChromaDB 多向量召回 key 的前端审核展示.
 *  Phase 3: 加"编辑全部"按钮 + Modal 多行 TextArea.
 * ════════════════════════════════════════════ */

import { useState } from "react";
import { Empty, Input, Modal, Tag, message } from "antd";
import { editKnowledge } from "@/api";

interface HQ {
  q: string;
  generated_at: string;
  model: string;
}

interface Props {
  entryId: number;
  hypothetical_queries_json: string;
  onUpdated?: () => void;
}

export function HypotheticalQueriesPanel({
  entryId,
  hypothetical_queries_json,
  onUpdated,
}: Props) {
  const [editOpen, setEditOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);

  let parsed: HQ[] = [];
  try {
    parsed = JSON.parse(hypothetical_queries_json || "[]");
  } catch {
    parsed = [];
  }

  const handleOpenEdit = () => {
    setDraft(parsed.map((p) => p.q).join("\n"));
    setEditOpen(true);
  };

  const handleSave = async () => {
    const hqs = draft
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);
    setSaving(true);
    try {
      await editKnowledge(entryId, {
        hypothetical_queries: hqs,
        reason: "manual edit HQ",
      });
      message.success("HQ 已更新");
      setEditOpen(false);
      onUpdated?.();
    } catch (e: unknown) {
      const detail =
        (e as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail ?? "保存失败";
      message.error(detail);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={{ marginTop: 8 }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 8,
        }}
      >
        <span style={{ fontSize: 12, color: "#999" }}>
          LLM 同步生成的假设触发问题, 用作 ChromaDB 多向量召回 key
        </span>
        <button
          onClick={handleOpenEdit}
          style={{
            padding: "4px 10px",
            borderRadius: 5,
            fontSize: 11,
            fontWeight: 500,
            cursor: "pointer",
            border: "1px solid #93c5fd",
            background: "#fff",
            color: "#2563eb",
            fontFamily: "inherit",
            lineHeight: 1.4,
          }}
        >
          编辑全部
        </button>
      </div>
      {parsed.length === 0 ? (
        <Empty description="未生成假设触发问题 (仅 rule / route_hint 启用)" />
      ) : (
        parsed.map((hq, i) => (
          <Tag key={i} color="blue" style={{ marginBottom: 4 }}>
            {hq.q}
          </Tag>
        ))
      )}

      <Modal
        title="编辑假设触发问题 (一行一条)"
        open={editOpen}
        onCancel={() => setEditOpen(false)}
        onOk={handleSave}
        confirmLoading={saving}
        destroyOnHidden
        width={600}
        okText="保存"
        cancelText="取消"
      >
        <Input.TextArea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={8}
          placeholder="一行一条 HQ"
        />
        <div style={{ marginTop: 8, fontSize: 12, color: "#999" }}>
          注意: 用户编辑跳过 LLM 路径校验, 请确保问题文本与 KE 路径连续匹配.
        </div>
      </Modal>
    </div>
  );
}
