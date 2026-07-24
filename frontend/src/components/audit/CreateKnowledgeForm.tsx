/* ════════════════════════════════════════════════════════════════════════════
 *  CreateKnowledgeForm — 按 entry_type 5 类自适应的添加知识 Modal
 *  ────────────────────────────────────────────────────────────────────────
 *  - terminology: 复用 TerminologyEditPanel (database 一级 + collection 二级 + db_type readOnly)
 *  - instance_alias: 别名 → 具体记录 (6 字段)
 *  - rule / example / route_hint: 同文件就近声明小型字段块
 *  - 提交时按类型派生 content (后端 KnowledgeEntryCreate.content 必填 min_length=1)
 *  - conflicts / overflow 通过 onSubmitted(response) 抛回父组件决定 Modal 关闭
 * ══════════════════════════════════════════════════════════════════════════ */

import React, { useEffect, useState } from "react";
import { Form, Input, InputNumber, Modal, Select, message } from "antd";
import * as api from "@/api";
import type { CollectionRef, DbType, KnowledgeEntryCreateResponse } from "@/types";
import { DB_TYPE_META } from "@/types";
import { RESULT_SUMMARY_MAX_LEN } from "./knowledgeConstants";
import { DatabaseCollectionPicker } from "./DatabaseCollectionPicker";
import TerminologyEditPanel, {
  type TerminologyPayload,
} from "./TerminologyEditPanel";

type EntryType =
  | "terminology" | "instance_alias" | "rule" | "example" | "route_hint";
type Scope = "global" | "namespace";
type Tier = "normal" | "critical";

/* ── entry_type 选项元信息 — 下拉 label 内短描述 + 选中后完整说明 ──
 * label: 中文名 + 一句话区分 (供下拉项快速辨别)
 * hint:  选中后展开的完整用途说明 (供填表前理解该填什么) */
const ENTRY_TYPE_META: Record<
  EntryType,
  { label: string; hint: string }
> = {
  terminology: {
    label: "业务术语 (名词 → 表/字段映射)",
    hint: "把业务里说的名词对应到具体的数据库表与字段。例如让系统知道用户口中的某个业务概念实际查哪张表的哪些列。",
  },
  instance_alias: {
    label: "实例别名 (简称 → 具体一条记录)",
    hint: "把用户常用的简称对应到数据库里某一条具体记录。例如某个常被简称提及的对象，绑定到它在表中的唯一 ID。",
  },
  rule: {
    label: "查询规则 (查询约束 / 默认行为)",
    hint: "给查询补充约束或默认行为。例如某类查询默认按某字段排序、默认只看某状态的数据，让生成的查询更符合业务习惯。",
  },
  example: {
    label: "示例查询 (问题 → 成功查询案例)",
    hint: "沉淀一个「自然语言问题 → 正确查询方案」的成功案例，供相似问题复用，提升后续生成的准确率。",
  },
  route_hint: {
    label: "路由偏好 (多表关联路径提示)",
    hint: "为涉及多表/多集合关联的问题, 手动录入推荐的关联路径与导航说明 (关联字段/关联类型/嵌套位置/避坑), 供相似问题复用.",
  },
};

interface Props {
  open: boolean;
  defaultNamespaceId: number | undefined;
  onClose: () => void;
  onSubmitted: (response: KnowledgeEntryCreateResponse) => void;
}

interface RulePayloadDraft {
  rule_text: string;
  applies_to_collections: CollectionRef[];
  priority: number;
}

interface ExamplePayloadDraft {
  question_pattern: string;
  collections: CollectionRef[];
  final_query_plan_text: string;
  result_summary: string;
}

interface RouteHintPayloadDraft {
  question_pattern: string;   // 提交为 content
  collection_path: CollectionRef[];
  navigation_note: string;
}

export default function CreateKnowledgeForm({
  open, defaultNamespaceId, onClose, onSubmitted,
}: Props) {
  const [entryType, setEntryType] = useState<EntryType>("terminology");
  const [scope, setScope] = useState<Scope>("namespace");
  const [tier, setTier] = useState<Tier>("normal");

  const [termPayload, setTermPayload] = useState<TerminologyPayload>({});
  const [iaPayload, setIaPayload] = useState<{
    alias: string; canonical_name: string; target_collection: string;
    target_database: string; target_id: string; id_field: string; db_type: string;
  }>({ alias: "", canonical_name: "", target_collection: "", target_database: "", target_id: "", id_field: "", db_type: "" });
  const [rulePayload, setRulePayload] = useState<RulePayloadDraft>({
    rule_text: "", applies_to_collections: [], priority: 0,
  });
  const [exPayload, setExPayload] = useState<ExamplePayloadDraft>({
    question_pattern: "", collections: [],
    final_query_plan_text: "", result_summary: "",
  });
  const [rhPayload, setRhPayload] = useState<RouteHintPayloadDraft>({
    question_pattern: "", collection_path: [], navigation_note: "",
  });
  const [jsonError, setJsonError] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);

  // Bug fix ZYZ-55: 每次打开弹窗（新建模式）时重置所有字段，避免带出上次的历史数据
  useEffect(() => {
    if (!open) return;
    setEntryType("terminology");
    setScope("namespace");
    setTier("normal");
    setTermPayload({});
    setIaPayload({ alias: "", canonical_name: "", target_collection: "", target_database: "", target_id: "", id_field: "", db_type: "" });
    setRulePayload({ rule_text: "", applies_to_collections: [], priority: 0 });
    setExPayload({ question_pattern: "", collections: [], final_query_plan_text: "", result_summary: "" });
    setRhPayload({ question_pattern: "", collection_path: [], navigation_note: "" });
    setJsonError("");
  }, [open]);

  const handleSubmit = async () => {
    const namespace_id = scope === "global" ? null : defaultNamespaceId ?? null;
    let body: Parameters<typeof api.createKnowledge>[0] | null = null;

    if (entryType === "terminology") {
      const p = termPayload;
      if (!p.term || !p.primary_database || !p.primary_collection || !p.db_type) {
        message.warning("term / database / collection / db_type 必填");
        return;
      }
      body = {
        entry_type: "terminology",
        namespace_id, tier,
        content: p.term,
        raw_input: p.term,
        payload: {
          term: p.term,
          primary_database: p.primary_database,
          primary_collection: p.primary_collection,
          db_type: p.db_type,
          synonyms: p.synonyms ?? [],
          source_collections: p.source_collections ?? [],
        },
      };
    } else if (entryType === "instance_alias") {
      const p = iaPayload;
      if (!p.alias || !p.target_database || !p.target_collection || !p.target_id || !p.db_type) {
        message.warning("alias / target_database / target_collection / target_id / db_type 必填");
        return;
      }
      if (namespace_id === null || namespace_id === undefined) {
        message.warning("instance_alias 必须挂在命名空间下");
        return;
      }
      body = {
        entry_type: "instance_alias",
        namespace_id, tier,
        content: p.alias,
        raw_input: p.alias,
        payload: {
          alias: p.alias,
          canonical_name: p.canonical_name || "",
          target_collection: p.target_collection,
          target_database: p.target_database,
          target_id: p.target_id,
          id_field: p.id_field || "_id",
          db_type: p.db_type,
        },
      };
    } else if (entryType === "rule") {
      if (!rulePayload.rule_text.trim()) {
        message.warning("rule_text 必填");
        return;
      }
      body = {
        entry_type: "rule",
        namespace_id, tier,
        content: rulePayload.rule_text,
        payload: {
          rule_text: rulePayload.rule_text,
          applies_to_collections: rulePayload.applies_to_collections,
          priority: rulePayload.priority,
        },
      };
    } else if (entryType === "example") {
      if (!exPayload.question_pattern) {
        message.warning("question_pattern 必填");
        return;
      }
      let planJson: object | null = null;
      if (exPayload.final_query_plan_text.trim()) {
        try {
          planJson = JSON.parse(exPayload.final_query_plan_text);
        } catch {
          setJsonError("final_query_plan 格式不合法");
          return;
        }
      }
      body = {
        entry_type: "example",
        namespace_id, tier,
        content: exPayload.question_pattern,
        payload: {
          question_pattern: exPayload.question_pattern,
          collections: exPayload.collections,
          join_keys: [],
          final_query_plan: planJson,
          result_summary: exPayload.result_summary,
        },
      };
    } else if (entryType === "route_hint") {
      if (!rhPayload.question_pattern || rhPayload.collection_path.length === 0) {
        message.warning("question_pattern / collection_path 必填");
        return;
      }
      body = {
        entry_type: "route_hint",
        namespace_id, tier,
        content: rhPayload.question_pattern,
        payload: {
          collection_path: rhPayload.collection_path,
          navigation_note: rhPayload.navigation_note,
        },
      };
    }

    if (!body) return;
    setSubmitting(true);
    try {
      const res = await api.createKnowledge(body);
      onSubmitted(res);
    } catch (err: unknown) {
      const e = err as { response?: { status?: number; data?: { detail?: string; overflow?: boolean } } };
      const data = e?.response?.data;
      if (e?.response?.status === 409 && data?.overflow) {
        onSubmitted(data as KnowledgeEntryCreateResponse);
        return;
      }
      message.error(data?.detail || "添加失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title="添加知识"
      open={open}
      onOk={handleSubmit}
      onCancel={onClose}
      width={640}
      confirmLoading={submitting}
      cancelButtonProps={{ disabled: submitting }}
      maskClosable={!submitting}
    >
      <Form layout="vertical">
        <Form.Item label="类型" required extra={ENTRY_TYPE_META[entryType].hint}>
          <Select
            aria-label="类型"
            value={entryType}
            onChange={(v) => setEntryType(v as EntryType)}
            options={(Object.keys(ENTRY_TYPE_META) as EntryType[]).map((k) => ({
              value: k,
              label: ENTRY_TYPE_META[k].label,
            }))}
          />
        </Form.Item>
        <Form.Item label="生效范围">
          <Select
            value={scope}
            onChange={(v) => setScope(v as Scope)}
            options={[
              { value: "namespace", label: "仅当前命名空间" },
              { value: "global", label: "全局 (所有命名空间共享)" },
            ]}
          />
        </Form.Item>
        <Form.Item label="优先级">
          <Select
            value={tier}
            onChange={(v) => setTier(v as Tier)}
            options={[
              { value: "normal", label: "普通 (向量召回)" },
              { value: "critical", label: "关键 (强约束 — 每次查询必注入)" },
            ]}
          />
        </Form.Item>

        {entryType === "terminology" && defaultNamespaceId !== undefined && (
          <TerminologyEditPanel
            nsId={defaultNamespaceId}
            value={termPayload}
            onChange={setTermPayload}
          />
        )}

        {entryType === "rule" && (
          <RuleFields nsId={defaultNamespaceId!} value={rulePayload} onChange={setRulePayload} />
        )}

        {entryType === "instance_alias" && defaultNamespaceId !== undefined && (
          <>
            <Form.Item label="别名" required>
              <Input
                value={iaPayload.alias}
                onChange={(e) => setIaPayload({ ...iaPayload, alias: e.target.value })}
                placeholder="用户问题里的简称, 如 黄金会员"
                maxLength={50}
              />
            </Form.Item>
            <Form.Item label="全名">
              <Input
                value={iaPayload.canonical_name}
                onChange={(e) => setIaPayload({ ...iaPayload, canonical_name: e.target.value })}
                placeholder="记录的全名, 供审核者识别"
              />
            </Form.Item>
            <Form.Item label="目标库 / 目标集合" required>
              <DatabaseCollectionPicker
                nsId={defaultNamespaceId!}
                mode="single"
                value={iaPayload.target_database
                  ? [{ database: iaPayload.target_database, collection: iaPayload.target_collection }]
                  : []}
                onChange={(refs) => setIaPayload((prev) => ({
                  ...prev,
                  target_database: refs[0]?.database ?? "",
                  target_collection: refs[0]?.collection ?? "",
                }))}
                onDbTypeChange={(dbType) => setIaPayload((prev) => ({
                  ...prev, db_type: dbType ?? "",
                }))}
              />
            </Form.Item>
            <Form.Item label="数据库类型" required>
              <Select
                aria-label="数据库类型"
                value={(iaPayload.db_type || undefined) as DbType | undefined}
                placeholder="选择数据库类型"
                onChange={(v: DbType) => setIaPayload({ ...iaPayload, db_type: v })}
                options={(Object.keys(DB_TYPE_META) as DbType[]).map((k) => ({
                  value: k,
                  label: DB_TYPE_META[k].label,
                }))}
                style={{ width: 200 }}
              />
            </Form.Item>
            <Form.Item label="记录 ID" required>
              <Input
                value={iaPayload.target_id}
                onChange={(e) => setIaPayload({ ...iaPayload, target_id: e.target.value })}
                placeholder="_id 或唯一键值"
              />
            </Form.Item>
            <Form.Item label="ID 字段名">
              <Input
                value={iaPayload.id_field}
                onChange={(e) => setIaPayload({ ...iaPayload, id_field: e.target.value })}
                placeholder="默认 _id, 自定义唯一键填实际字段名"
              />
            </Form.Item>
          </>
        )}

        {entryType === "example" && (
          <ExampleFields nsId={defaultNamespaceId!}
            value={exPayload} onChange={setExPayload}
            jsonError={jsonError} clearJsonError={() => setJsonError("")}
          />
        )}

        {entryType === "route_hint" && (
          <RouteHintFields nsId={defaultNamespaceId!} value={rhPayload} onChange={setRhPayload} />
        )}
      </Form>
    </Modal>
  );
}

function RuleFields({
  nsId, value, onChange,
}: { nsId: number; value: RulePayloadDraft; onChange: (v: RulePayloadDraft) => void }) {
  return (
    <>
      <Form.Item label="规则文本" required>
        <Input.TextArea
          aria-label="规则文本"
          rows={4}
          value={value.rule_text}
          onChange={(e) => onChange({ ...value, rule_text: e.target.value })}
          placeholder="例: 查询订单时, 默认按下单时间倒序"
        />
      </Form.Item>
      <Form.Item label="适用集合 (可选)">
        <DatabaseCollectionPicker
          nsId={nsId}
          mode="multiple"
          value={value.applies_to_collections}
          onChange={(refs) => onChange({ ...value, applies_to_collections: refs })}
        />
      </Form.Item>
      <Form.Item label="优先级 (可选, 默认 0)">
        <InputNumber
          aria-label="优先级"
          value={value.priority}
          onChange={(n) => onChange({ ...value, priority: n ?? 0 })}
          min={0}
        />
      </Form.Item>
    </>
  );
}

function ExampleFields({
  nsId, value, onChange, jsonError, clearJsonError,
}: {
  nsId: number;
  value: ExamplePayloadDraft;
  onChange: (v: ExamplePayloadDraft) => void;
  jsonError: string;
  clearJsonError: () => void;
}) {
  return (
    <>
      <Form.Item label="问题模式" required>
        <Input.TextArea
          aria-label="问题模式"
          rows={2}
          value={value.question_pattern}
          onChange={(e) => onChange({ ...value, question_pattern: e.target.value })}
          placeholder="语义骨架, 例: 按某状态分组统计某时段内的订单数"
        />
      </Form.Item>
      <Form.Item label="涉及集合 (可选)">
        <DatabaseCollectionPicker
          nsId={nsId}
          mode="multiple"
          value={value.collections}
          onChange={(refs) => onChange({ ...value, collections: refs })}
        />
      </Form.Item>
      <Form.Item
        label="查询计划 (JSON, 可选)"
        validateStatus={jsonError ? "error" : ""}
        help={jsonError}
      >
        <Input.TextArea
          aria-label="查询计划"
          rows={6}
          value={value.final_query_plan_text}
          onChange={(e) => {
            clearJsonError();
            onChange({ ...value, final_query_plan_text: e.target.value });
          }}
          placeholder='{"steps": [{"db_type": "mysql", ...}]}'
        />
      </Form.Item>
      <Form.Item label={`结果摘要 (可选，最多 ${RESULT_SUMMARY_MAX_LEN} 字)`}>
        <Input.TextArea
          aria-label="result_summary"
          rows={2}
          value={value.result_summary}
          onChange={(e) => onChange({ ...value, result_summary: e.target.value })}
          maxLength={RESULT_SUMMARY_MAX_LEN}
          showCount
        />
      </Form.Item>
    </>
  );
}

function RouteHintFields({
  nsId, value, onChange,
}: { nsId: number; value: RouteHintPayloadDraft; onChange: (v: RouteHintPayloadDraft) => void }) {
  return (
    <>
      <Form.Item label="问题模式" required>
        <Input
          aria-label="问题模式"
          value={value.question_pattern}
          onChange={(e) => onChange({ ...value, question_pattern: e.target.value })}
          placeholder="问题模式, 例: 查 X 关联的 Y"
        />
      </Form.Item>
      <Form.Item label="集合路径 (有序)" required>
        <DatabaseCollectionPicker
          nsId={nsId}
          mode="multiple"
          value={value.collection_path}
          onChange={(cp) => onChange({ ...value, collection_path: cp })}
        />
      </Form.Item>
      <Form.Item label="导航说明 (关联字段 / 关联类型 / 嵌套位置 / 避坑)">
        <Input.TextArea
          aria-label="导航说明"
          rows={3}
          value={value.navigation_note}
          onChange={(e) => onChange({ ...value, navigation_note: e.target.value })}
          placeholder="orders.items[].sku ↔ products.sku (nested_array, 非 products.id); 类别在 products.categories[] 需 $unwind"
        />
      </Form.Item>
    </>
  );
}

