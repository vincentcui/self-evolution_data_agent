/* ════════════════════════════════════════════
 *  EditCanonicalForm — canonical 编辑 (debounce 冲突检测)
 *  Phase 3 Task 3.2: terminology 类型挂 TerminologyEditPanel + payload 提交
 * ════════════════════════════════════════════ */

import React, { useEffect, useMemo, useState } from "react";
import { Badge, Button, Collapse, Form, Input, Select, Space, Tag, message } from "antd";
import { editKnowledge, previewConflict } from "@/api";
import type { KnowledgeEntry } from "@/types";
import ConflictDiff from "./ConflictDiff";
import TerminologyEditPanel, { type TerminologyPayload } from "./TerminologyEditPanel";
import ExampleEditPanel, { type ExamplePayload } from "./ExampleEditPanel";
import RouteHintEditPanel, { type RouteHintPayload } from "./RouteHintEditPanel";

interface Props {
  entry: KnowledgeEntry;
  onDone?: () => void;
}

// keep in sync with backend IS_TERMINOLOGY_TERM_MAX_LEN (default 20); UX hint only — backend re-validates
const TERM_MAX_LEN = 20;

// ── terminology 初始 payload — 优先 entry.payload (后端 JSON 解析后注入),
//    旧路径兜底解析 entry.content (历史 KE content 曾是 JSON 字符串).
function initialTerminologyPayload(entry: KnowledgeEntry): TerminologyPayload {
  if (entry.payload && typeof entry.payload === "object") {
    return entry.payload as TerminologyPayload;
  }
  try {
    const obj = JSON.parse(entry.content);
    return obj && typeof obj === "object" ? obj : {};
  } catch {
    return {};
  }
}

function validateTerm(term: string | undefined): string | undefined {
  if (!term) return undefined;
  const t = term.trim();
  if (!t) return undefined;
  if (t.length > TERM_MAX_LEN) {
    return `术语应为单一业务名词 (不能超过 ${TERM_MAX_LEN} 字), 当前 ${t.length} 字`;
  }
  if (t.includes("\n")) return "术语不应含换行";
  if (/[。；;]/.test(t)) return "术语不应含句号/分号";
  return undefined;
}

export default function EditCanonicalForm({ entry, onDone }: Props) {
  const isTerminology = entry.entry_type === "terminology";
  const isExample     = entry.entry_type === "example";
  const isRouteHint   = entry.entry_type === "route_hint";

  const [content, setContent] = useState(entry.content);
  const [tier, setTier] = useState<string>(entry.tier);
  const [reason, setReason] = useState("");
  const [conflicts, setConflicts] = useState<any[]>([]);
  const [submitting, setSubmitting] = useState(false);

  // ── terminology 状态: payload + term 校验错误 ──
  const initialPayload = useMemo<TerminologyPayload>(
    () => (isTerminology ? initialTerminologyPayload(entry) : {}),
    [isTerminology, entry],
  );
  const [payload, setPayload] = useState<TerminologyPayload>(initialPayload);
  const [termError, setTermError] = useState<string | undefined>(undefined);

  // ── example 状态: 对齐后端 ExamplePayload schema ──
  const [examplePayload, setExamplePayload] = useState<ExamplePayload>(() => {
    const p = (entry.payload ?? {}) as Record<string, unknown>;
    return {
      ...p,
      question_pattern:  p.question_pattern as string ?? p.question as string ?? entry.content ?? "",
      collections:       (p.collections as string[]) ?? [],
      join_keys:         (p.join_keys as Record<string, unknown>[]) ?? [],
      final_query_plan:  (p.final_query_plan as Record<string, unknown>) ?? (p.query_json as Record<string, unknown>) ?? null,
      result_summary:    (p.result_summary as string) ?? "",
      // legacy passthrough
      question:          p.question as string,
      target_collection: p.target_collection as string,
      query_json:        p.query_json as Record<string, unknown>,
    } as ExamplePayload;
  });

  // ── route_hint 状态: 路径/连接/策略来自 trace, reason 可编辑 ──
  const [routeHintPayload, setRouteHintPayload] = useState<RouteHintPayload>(() => {
    const p = (entry.payload ?? {}) as Partial<RouteHintPayload>;
    return {
      collection_path: Array.isArray(p.collection_path) ? p.collection_path : [],
      join_fields:     Array.isArray(p.join_fields) ? p.join_fields : [],
      cost_strategy:   p.cost_strategy ?? "default",
      reason:          p.reason ?? "",
    };
  });

  // ── payload 变化 → 同步 content (JSON.stringify) 用于冲突检测 ──
  useEffect(() => {
    if (!isTerminology) return;
    setContent(JSON.stringify(payload));
  }, [isTerminology, payload]);

  // ── 实时冲突检测 (debounce 300ms) ──
  useEffect(() => {
    if (content === entry.content) { setConflicts([]); return; }
    const t = setTimeout(async () => {
      try {
        const r = await previewConflict({
          namespace_id: entry.namespace_id,
          entry_type: entry.entry_type,
          content, entry_id: entry.id,
        });
        setConflicts(r.conflicts);
      } catch { /* silent */ }
    }, 300);
    return () => clearTimeout(t);
  }, [content, entry.content, entry.entry_type, entry.id, entry.namespace_id]);

  const handleSubmit = async () => {
    if (!reason.trim()) { message.warning("reason 必填"); return; }
    if (isTerminology && termError) { message.warning(termError); return; }
    setSubmitting(true);
    try {
      if (isExample) {
        const q = (examplePayload.question_pattern as string || examplePayload.question as string || "").trim();
        if (!q) {
          message.warning("问题模式不能为空");
          setSubmitting(false);
          return;
        }
        await editKnowledge(entry.id, {
          payload: examplePayload, content: q, tier, reason,
        });
      } else if (isRouteHint) {
        // ── route_hint: content 沿用 entry.content (route_hint.content 必须与对应
        //    example.content 一致, UI 不允许修改) ──
        await editKnowledge(entry.id, {
          payload: routeHintPayload, content: entry.content, tier, reason,
        });
      } else if (isTerminology) {
        // ── terminology: payload.term 同步写入 content (RAG 索引文本 = term).
        //    若不同步, ChromaDB 会按旧 term 召回, 列表卡片读 content 也显示旧值.
        const term = (payload.term ?? "").trim();
        await editKnowledge(entry.id, {
          payload, content: term, tier, reason,
        });
      } else {
        await editKnowledge(entry.id, { content, tier, reason });
      }
      message.success("已编辑");
      onDone?.();
    } catch (e: any) {
      message.error(e?.response?.data?.detail ?? "编辑失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Form layout="vertical">
      {isExample ? (
        <ExampleEditPanel value={examplePayload} onChange={setExamplePayload} />
      ) : isRouteHint ? (
        <RouteHintEditPanel value={routeHintPayload} onChange={setRouteHintPayload} />
      ) : isTerminology && entry.namespace_id != null ? (
        <TerminologyEditPanel
          nsId={entry.namespace_id}
          value={payload}
          onChange={setPayload}
          termError={termError}
          onTermBlur={(t) => setTermError(validateTerm(t))}
        />
      ) : (
        <Form.Item label="内容">
          <Input.TextArea rows={6} value={content} onChange={(e) => setContent(e.target.value)} />
        </Form.Item>
      )}

      <Form.Item label="级别">
        <Select value={tier} onChange={setTier}
          options={[
            { label: "普通 (向量召回)", value: "normal" },
            { label: "关键 (强约束 — 每次查询必注入)", value: "critical" },
          ]} style={{ width: 240 }} />
      </Form.Item>
      <Form.Item label="编辑原因" required>
        <Input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="为何修改" />
      </Form.Item>

      <ConflictDiff conflicts={conflicts} />

      <Space style={{ marginTop: 16 }}>
        <Button type="primary" loading={submitting} onClick={handleSubmit}>
          保存
        </Button>
        {conflicts.length > 0 && <Badge count={conflicts.length} title="冲突" />}
      </Space>

      {/* ── dynamic_variants 折叠区 (mybatis_extract 专用) ── */}
      {isExample && entry.payload?.extraction_source === "mybatis_extract" && (
        <>
          <div style={{ background: "#fafafa", padding: 8, marginTop: 16, fontSize: 12, borderRadius: 4 }}>
            来源: {String(entry.payload.source_mapper ?? "")}.{String(entry.payload.source_method ?? "")}
            {entry.payload.source_repo_id != null && ` (repo #${entry.payload.source_repo_id})`}
            {" | "}
            EXPLAIN 验证: {entry.payload.explain_verified ? "✓ 通过" : "✗ 未通过"}
          </div>
          {Array.isArray(entry.payload.dynamic_variants) && (entry.payload.dynamic_variants as any[]).length > 0 && (
            <Collapse
              size="small"
              style={{ marginTop: 8 }}
              items={[{
                key: "variants",
                label: `动态分支 (${(entry.payload.dynamic_variants as any[]).length})`,
                children: (
                  <Space direction="vertical" style={{ width: "100%" }}>
                    {(entry.payload.dynamic_variants as any[]).map((v: any, i: number) => (
                      <div key={i} style={{ borderBottom: "1px solid #f0f0f0", paddingBottom: 8 }}>
                        <strong>branch {i + 1}:</strong> {Array.isArray(v.branch_conditions) ? v.branch_conditions.join(" AND ") : ""}
                        {v.verified && <Tag color="green" style={{ marginLeft: 8 }}>verified ✓</Tag>}
                        <pre style={{ background: "#f5f5f5", padding: 8, marginTop: 4, fontSize: 12 }}>{v.sql}</pre>
                      </div>
                    ))}
                  </Space>
                ),
              }]}
            />
          )}
        </>
      )}
    </Form>
  );
}
