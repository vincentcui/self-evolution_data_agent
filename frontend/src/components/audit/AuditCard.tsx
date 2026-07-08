/* ════════════════════════════════════════════
 *  AuditCard — 单条审核卡 (对齐 knowledge-manager 设计稿)
 *  布局: [ID] [body: header标签行 / title / path / summary] [side: time / actions]
 * ════════════════════════════════════════════ */

import React, { useState } from "react";
import { Checkbox, Modal, message } from "antd";
import {
  approveEntry, deleteKnowledgeWithMode, rejectEntry, restoreEntry,
} from "@/api";
import type { KnowledgeEntry } from "@/types";
import EditCanonicalForm from "./EditCanonicalForm";
import AuditLogTimeline from "./AuditLogTimeline";
import { HypotheticalQueriesPanel } from "./HypotheticalQueriesPanel";
import { RelatedEntriesPanel } from "./RelatedEntriesPanel";
import s from "./AuditCard.module.css";

const STATUS_LABELS: Record<string, string> = {
  proposed: "待审", canonical: "已通过",
  rejected: "已拒绝", superseded: "已替代",
};
const STATUS_CLS: Record<string, string> = {
  proposed: s.sProposed, canonical: s.sCanonical,
  rejected: s.sRejected, superseded: s.sSuperseded,
};

const ENTRY_TYPE_LABELS: Record<string, string> = {
  terminology:    "业务术语",
  instance_alias: "实例别名",
  example:        "示例查询",
  rule:           "查询规则",
  route_hint:     "路由偏好",
};
const ENTRY_TYPE_CLS: Record<string, string> = {
  terminology:    s.pillTerminology,
  instance_alias: s.pillInstanceAlias,
  example:        s.pillExample,
  rule:           s.pillRule,
  route_hint:     s.pillRouteHint,
};

const SOURCE_LABELS: Record<string, string> = {
  schema:        "Schema 抽取",
  manual:        "手动",
  agent_learn:   "Agent 学习",
  code_extract:  "代码提取",
};

interface Props {
  entry: KnowledgeEntry;
  selectable?: boolean;
  selected?: boolean;
  onSelect?: (checked: boolean) => void;
  onAction?: () => void;
}

export default function AuditCard({
  entry, selectable, selected, onSelect, onAction,
}: Props) {
  const [editOpen, setEditOpen] = useState(false);
  const [logOpen, setLogOpen] = useState(false);
  const [approving, setApproving] = useState(false);

  const handleApprove = async () => {
    setApproving(true);
    try {
      await approveEntry(entry.id);
      message.success("审核通过");
      onAction?.();
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? "通过失败");
    } finally {
      setApproving(false);
    }
  };

  const handleReject = () => {
    Modal.confirm({
      title: "拒绝条目", okText: "拒绝", okType: "danger",
      content: (
        <input id="reject-reason" placeholder="拒绝原因 (必填)"
          style={{ width: "100%", padding: 6, marginTop: 8 }} />
      ),
      onOk: async () => {
        const reason = (document.getElementById("reject-reason") as HTMLInputElement)?.value?.trim();
        if (!reason) { message.warning("原因必填"); return Promise.reject(); }
        await rejectEntry(entry.id, reason);
        message.success("已拒绝");
        onAction?.();
      },
    });
  };

  const handleRestore = async () => {
    const reason = window.prompt("恢复原因 (必填)");
    if (!reason?.trim()) return;
    try {
      await restoreEntry(entry.id, reason);
      message.success("已恢复");
      onAction?.();
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? "恢复失败");
    }
  };

  const handleSoftDelete = () => {
    const reason = window.prompt("删除原因 (必填)");
    if (!reason?.trim()) return;
    deleteKnowledgeWithMode(entry.id, "soft", reason).then(() => {
      message.success("已下架");
      onAction?.();
    }).catch((e) => {
      message.error(e?.response?.data?.detail ?? "下架失败");
    });
  };

  const createdAtShort = entry.created_at ? entry.created_at.slice(0, 10) : "";
  const p = (entry.payload ?? {}) as Record<string, unknown>;

  // 路径节点（route_hint 的 collection_path 或 example 的 collections）
  const pathNodes: string[] = (() => {
    if (entry.entry_type === "route_hint") {
      return (p.collection_path as string[]) ?? [];
    }
    if (entry.entry_type === "example") {
      return (p.collections as string[]) ?? [];
    }
    return [];
  })();

  // 摘要文字
  const summary: string = (() => {
    if (entry.entry_type === "example") return (p.result_summary as string) ?? "";
    if (entry.entry_type === "route_hint") return (p.reason as string) ?? "";
    return entry.description ?? "";
  })();

  // 术语信息 chips
  const termChips: string[] = (() => {
    if (entry.entry_type !== "terminology") return [];
    const out: string[] = [];
    const coll = p.primary_collection as string | undefined;
    const db = p.primary_database as string | undefined;
    const dbType = p.db_type as string | undefined;
    if (coll) out.push(coll);
    if (db) out.push(db);
    if (dbType) out.push(dbType);
    return out;
  })();

  // instance_alias chip
  const aliasChip: string | null = (() => {
    if (entry.entry_type !== "instance_alias") return null;
    const db = p.target_database as string | undefined;
    const coll = p.target_collection as string | undefined;
    const idField = p.id_field as string | undefined;
    const id = p.target_id as string | undefined;
    return [db, coll, `${idField ?? "_id"}=${id ?? "?"}`].filter(Boolean).join(" · ");
  })();

  return (
    <div className={s.entryRow}>
      {/* 中间内容 */}
      <div className={s.entryBody}>
        {/* 标签行 */}
        <div className={s.entryHeader}>
          {selectable && (
            <Checkbox checked={selected} onChange={(e) => onSelect?.(e.target.checked)} />
          )}
          <span className={`${s.pill} ${ENTRY_TYPE_CLS[entry.entry_type] ?? ""}`}>
            {ENTRY_TYPE_LABELS[entry.entry_type] ?? entry.entry_type}
          </span>
          <span className={s.dotSep}>·</span>
          <span className={`${s.statusDot} ${STATUS_CLS[entry.status] ?? ""}`}>
            {STATUS_LABELS[entry.status] ?? entry.status}
          </span>
          <span className={s.dotSep}>·</span>
          <span className={s.sourceLabel}>
            {SOURCE_LABELS[entry.source] ?? entry.source}
          </span>
        </div>

        {/* 标题 */}
        <div className={s.entryTitle}>{entry.content}</div>

        {/* 路径节点 */}
        {pathNodes.length > 0 && (
          <div className={s.entryPath}>
            {pathNodes.map((n, i, arr) => (
              <React.Fragment key={`${n}-${i}`}>
                <span className={s.pnode}>{n}</span>
                {i < arr.length - 1 && <span className={s.parrow}>→</span>}
              </React.Fragment>
            ))}
          </div>
        )}

        {/* 术语 chips */}
        {termChips.length > 0 && (
          <div className={s.entryTermInfo}>
            {termChips.map((c) => <span key={c} className={s.termChip}>{c}</span>)}
            {((p.synonyms as string[]) ?? []).length > 0 && (
              <span style={{ color: "#9ba5b2" }}>
                同义词: {((p.synonyms as string[]) ?? []).join(", ")}
              </span>
            )}
          </div>
        )}

        {/* instance_alias chip */}
        {aliasChip && (
          <div className={s.entryTermInfo}>
            <span className={s.termChip}>{aliasChip}</span>
          </div>
        )}

        {/* 摘要 */}
        {summary && <div className={s.entrySummary}>{summary}</div>}

        {/* HQ + 关联条目（保留原有功能） */}
        {["rule", "route_hint"].includes(entry.entry_type) && (
          <HypotheticalQueriesPanel
            entryId={entry.id}
            hypothetical_queries_json={entry.hypothetical_queries_json ?? "[]"}
            onUpdated={onAction}
          />
        )}
        {entry.related_entry_ids_json && entry.related_entry_ids_json !== "[]" && (
          <RelatedEntriesPanel related_entry_ids_json={entry.related_entry_ids_json} />
        )}
      </div>

      {/* 右侧 时间 + 操作 */}
      <div className={s.entrySide}>
        {createdAtShort && <div className={s.entryTime}>{createdAtShort}</div>}
        <div className={s.entryActions}>
          {entry.status === "proposed" && (
            <>
              <button className={`${s.aBtn} ${s.aBtnPass}`} disabled={approving} onClick={handleApprove}>
                {approving ? "通过中..." : "通过"}
              </button>
              <button className={`${s.aBtn} ${s.aBtnEdit}`} onClick={() => setEditOpen(true)}>编辑</button>
              <button className={`${s.aBtn} ${s.aBtnReject}`} onClick={handleReject}>拒绝</button>
            </>
          )}
          {entry.status === "canonical" && (
            <>
              <button className={`${s.aBtn} ${s.aBtnEdit}`} onClick={() => setEditOpen(true)}>编辑</button>
              <button className={`${s.aBtn} ${s.aBtnReject}`} onClick={handleSoftDelete}>下架</button>
            </>
          )}
          {entry.status === "rejected" && (
            <button className={`${s.aBtn} ${s.aBtnRecover}`} onClick={handleRestore}>恢复</button>
          )}
          <button className={`${s.aBtn} ${s.aBtnLog}`} onClick={() => setLogOpen(true)}>审计日志</button>
        </div>
      </div>

      <Modal title="编辑知识条目" open={editOpen} onCancel={() => setEditOpen(false)}
        footer={null} destroyOnClose width={720}>
        <EditCanonicalForm entry={entry}
          onDone={() => { setEditOpen(false); onAction?.(); }} />
      </Modal>
      <Modal title="审计时间线" open={logOpen} onCancel={() => setLogOpen(false)}
        footer={null} destroyOnClose width={640}>
        <AuditLogTimeline entryId={entry.id} />
      </Modal>
    </div>
  );
}
