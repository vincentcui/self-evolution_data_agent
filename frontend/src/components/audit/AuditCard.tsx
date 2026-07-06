/* ════════════════════════════════════════════
 *  AuditCard — 单条审核卡 (proposed/canonical/rejected 行为分支)
 * ════════════════════════════════════════════ */

import React, { useState } from "react";
import { Button, Card, Checkbox, Modal, Space, Tag, Typography, message } from "antd";
import {
  approveEntry, deleteKnowledgeWithMode, rejectEntry, restoreEntry,
} from "@/api";
import type { KnowledgeEntry } from "@/types";
import { DB_TYPE_META } from "@/types";
import EditCanonicalForm from "./EditCanonicalForm";
import AuditLogTimeline from "./AuditLogTimeline";
import { HypotheticalQueriesPanel } from "./HypotheticalQueriesPanel";
import { RelatedEntriesPanel } from "./RelatedEntriesPanel";

const { Paragraph, Text } = Typography;

const STATUS_COLORS: Record<string, string> = {
  proposed: "orange", canonical: "green",
  rejected: "red", superseded: "default",
};

// ── 字段中文映射 — AuditQueue STATUS_OPTIONS 同源, 此处用于卡片 Tag 文本 ──
const STATUS_LABELS: Record<string, string> = {
  proposed: "待审", canonical: "已通过",
  rejected: "已拒绝", superseded: "已替代",
};

const ENTRY_TYPE_LABELS: Record<string, string> = {
  terminology:    "业务术语",
  instance_alias: "实例别名",
  example:        "示例查询",
  rule:           "查询规则",
  route_hint:     "路由偏好",
};

const SOURCE_LABELS: Record<string, string> = {
  schema:        "Schema 抽取",
  manual:        "手动",
  agent_learn:   "Agent 学习",
  code_extract: "代码提取",
};

const TIER_LABELS: Record<string, string> = {
  normal:   "普通",
  critical: "关键",
};

// ── entry_type 颜色映射: UI 上显式呈现 6 类宪章边界, 不依赖默认灰 Tag 视觉混淆 ──
const ENTRY_TYPE_COLORS: Record<string, string> = {
  terminology:    "geekblue",
  instance_alias: "purple",
  example:        "green",
  rule:           "orange",
  route_hint:     "cyan",
};

interface Props {
  entry: KnowledgeEntry;
  selectable?: boolean;
  selected?: boolean;
  onSelect?: (checked: boolean) => void;
  onAction?: () => void;  // 任何动作后回调
}

// ── terminology 路由展示 — 让审核者一眼看清条目对应的库表 ────────────────
function TerminologyRouting({ payload }: { payload: Record<string, unknown> | null }) {
  if (!payload) return null;
  const db = payload.primary_database as string | undefined;
  const coll = payload.primary_collection as string | undefined;
  const dbType = payload.db_type as string | undefined;
  const synonyms = (payload.synonyms as string[] | undefined) ?? [];
  const sourceColls = (payload.source_collections as string[] | undefined) ?? [];
  if (!db && !coll && !dbType && synonyms.length === 0) return null;
  return (
    <div style={{ marginBottom: 8, fontSize: 12 }}>
      <Space size="small" wrap>
        {dbType && <Tag color={DB_TYPE_META[dbType as keyof typeof DB_TYPE_META]?.color ?? "purple"}>{dbType}</Tag>}
        {db && <Text type="secondary">数据库: <Text code>{db}</Text></Text>}
        {coll && <Text type="secondary">集合: <Text code>{coll}</Text></Text>}
      </Space>
      {synonyms.length > 0 && (
        <div style={{ marginTop: 4 }}>
          <Text type="secondary">同义词: </Text>
          {synonyms.map((s) => <Tag key={s}>{s}</Tag>)}
        </div>
      )}
      {sourceColls.length > 1 && (
        <div style={{ marginTop: 4 }}>
          <Text type="secondary">关联集合: </Text>
          {sourceColls.map((c) => <Tag key={c}>{c}</Tag>)}
        </div>
      )}
    </div>
  );
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

  return (
    <Card size="small">
      <Space style={{ marginBottom: 8 }}>
        {selectable && (
          <Checkbox checked={selected} onChange={(e) => onSelect?.(e.target.checked)} />
        )}
        <Tag color={STATUS_COLORS[entry.status] ?? "default"}>{STATUS_LABELS[entry.status] ?? entry.status}</Tag>
        <Tag color={ENTRY_TYPE_COLORS[entry.entry_type] ?? "default"}>{ENTRY_TYPE_LABELS[entry.entry_type] ?? entry.entry_type}</Tag>
        <Tag>{SOURCE_LABELS[entry.source] ?? entry.source}</Tag>
        <Tag color={entry.tier === "critical" ? "magenta" : "blue"}>{TIER_LABELS[entry.tier] ?? entry.tier}</Tag>
      </Space>
      <Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 8 }}>
        {entry.content}
      </Paragraph>
      {entry.entry_type === "terminology" && (
        <TerminologyRouting payload={entry.payload} />
      )}
      {entry.entry_type === "instance_alias" && entry.payload && (
        <div style={{ marginBottom: 8, fontSize: 12 }}>
          <Space size="small" wrap>
            <Tag color="purple">
              {(entry.payload as Record<string, string>).target_database || "?"} /{" "}
              {(entry.payload as Record<string, string>).target_collection || "?"}
              {" · "}
              {(entry.payload as Record<string, string>).id_field || "_id"} ={" "}
              {(entry.payload as Record<string, string>).target_id || "?"}
            </Tag>
          </Space>
        </div>
      )}
      {entry.entry_type === "example" && entry.payload && (() => {
        const p = entry.payload as Record<string, unknown>;
        const targetCollection = (p.target_collection as string) ?? "";
        const targetDatabase = (p.target_database as string) ?? "";
        const resultSummary = (p.result_summary as string) ?? "";
        return (
          <div style={{ marginBottom: 8, fontSize: 12 }}>
            <Space size="small" wrap>
              {targetCollection && <Tag color="blue">{targetCollection}</Tag>}
              {targetDatabase && <Tag color="cyan">{targetDatabase}</Tag>}
            </Space>
            {resultSummary && (
              <div style={{ marginTop: 4 }}>
                <Text type="secondary">{resultSummary}</Text>
              </div>
            )}
          </div>
        );
      })()}
      {entry.entry_type === "route_hint" && entry.payload && (() => {
        const p = entry.payload as Record<string, unknown>;
        const path = (p.collection_path as string[]) ?? [];
        const joins = (p.join_fields as Array<{ a: string; b: string }>) ?? [];
        return (
          <div style={{ marginBottom: 8, fontSize: 12 }}>
            <div>
              <Text type="secondary">路径: </Text>
              {path.map((c, i, arr) => (
                <span key={c}>
                  <Tag color="cyan">{c}</Tag>
                  {i < arr.length - 1 && <Text type="secondary"> → </Text>}
                </span>
              ))}
            </div>
            {joins.length > 0 && (
              <div style={{ marginTop: 4 }}>
                <Text type="secondary">连接: </Text>
                {joins.map((j, i) => (
                  <Tag key={`${j.a}:${j.b}:${i}`}>{j.a} ↔ {j.b}</Tag>
                ))}
              </div>
            )}
            <div style={{ marginTop: 4 }}>
              <Tag>策略: {(p.cost_strategy as string) ?? "?"}</Tag>
              <Text type="secondary" style={{ marginLeft: 8 }}>
                {(p.reason as string) ?? ""}
              </Text>
            </div>
          </div>
        );
      })()}
      {entry.description && (
        <Paragraph type="secondary" style={{ marginBottom: 8 }}>
          {entry.description}
        </Paragraph>
      )}
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
      <Space>
        {entry.status === "proposed" && (
          <>
            <Button type="primary" size="small" loading={approving} onClick={handleApprove}>通过</Button>
            <Button size="small" onClick={() => setEditOpen(true)}>编辑</Button>
            <Button size="small" danger onClick={handleReject}>拒绝</Button>
          </>
        )}
        {entry.status === "canonical" && (
          <>
            <Button size="small" onClick={() => setEditOpen(true)}>编辑</Button>
            <Button size="small" danger onClick={handleSoftDelete}>下架</Button>
          </>
        )}
        {entry.status === "rejected" && (
          <Button size="small" onClick={handleRestore}>恢复</Button>
        )}
        <Button size="small" onClick={() => setLogOpen(true)}>审计日志</Button>
      </Space>

      <Modal title="编辑知识条目" open={editOpen} onCancel={() => setEditOpen(false)}
        footer={null} destroyOnClose width={720}>
        <EditCanonicalForm entry={entry}
          onDone={() => { setEditOpen(false); onAction?.(); }} />
      </Modal>
      <Modal title="审计时间线" open={logOpen} onCancel={() => setLogOpen(false)}
        footer={null} destroyOnClose width={640}>
        <AuditLogTimeline entryId={entry.id} />
      </Modal>
    </Card>
  );
}
