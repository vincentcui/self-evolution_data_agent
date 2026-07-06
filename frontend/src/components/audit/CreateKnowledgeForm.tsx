/* ════════════════════════════════════════════════════════════════════════════
 *  CreateKnowledgeForm — 按 entry_type 5 类自适应的添加知识 Modal
 *  ────────────────────────────────────────────────────────────────────────
 *  - terminology: 复用 TerminologyEditPanel (database 一级 + collection 二级 + db_type readOnly)
 *  - instance_alias: 别名 → 具体记录 (6 字段)
 *  - rule / example / route_hint: 同文件就近声明小型字段块
 *  - 提交时按类型派生 content (后端 KnowledgeEntryCreate.content 必填 min_length=1)
 *  - conflicts / overflow 通过 onSubmitted(response) 抛回父组件决定 Modal 关闭
 * ══════════════════════════════════════════════════════════════════════════ */

import React, { useState } from "react";
import { Form, Input, InputNumber, Modal, Select, message } from "antd";
import * as api from "@/api";
import type { KnowledgeEntryCreateResponse } from "@/types";
import TerminologyEditPanel, {
  type TerminologyPayload,
} from "./TerminologyEditPanel";

type EntryType =
  | "terminology" | "instance_alias" | "rule" | "example" | "route_hint";
type Scope = "global" | "namespace";
type Tier = "normal" | "critical";

interface Props {
  open: boolean;
  defaultNamespaceId: number | undefined;
  onClose: () => void;
  onSubmitted: (response: KnowledgeEntryCreateResponse) => void;
}

interface RulePayloadDraft {
  rule_text: string;
  applies_to_collections: string[];
  priority: number;
}

interface ExamplePayloadDraft {
  question: string;
  target_collection: string;
  target_database: string;
  query_json_text: string;
  result_summary: string;
}

interface RouteHintPayloadDraft {
  question_pattern: string;
  collection_path: string[];
  cost_strategy: string;
  reason: string;
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
    target_database: string; target_id: string; id_field: string;
  }>({ alias: "", canonical_name: "", target_collection: "", target_database: "", target_id: "", id_field: "" });
  const [rulePayload, setRulePayload] = useState<RulePayloadDraft>({
    rule_text: "", applies_to_collections: [], priority: 0,
  });
  const [exPayload, setExPayload] = useState<ExamplePayloadDraft>({
    question: "", target_collection: "", target_database: "",
    query_json_text: "", result_summary: "",
  });
  const [rhPayload, setRhPayload] = useState<RouteHintPayloadDraft>({
    question_pattern: "", collection_path: [], cost_strategy: "default", reason: "",
  });
  const [jsonError, setJsonError] = useState<string>("");
  const [submitting, setSubmitting] = useState(false);

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
      if (!p.alias || !p.target_database || !p.target_collection || !p.target_id) {
        message.warning("alias / target_database / target_collection / target_id 必填");
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
        payload: { ...rulePayload },
      };
    } else if (entryType === "example") {
      if (!exPayload.question || !exPayload.target_collection || !exPayload.query_json_text) {
        message.warning("question / target_collection / query_json 必填");
        return;
      }
      let queryJson: object;
      try {
        queryJson = JSON.parse(exPayload.query_json_text);
      } catch {
        setJsonError("query_json 格式不合法");
        return;
      }
      body = {
        entry_type: "example",
        namespace_id, tier,
        content: exPayload.question,
        payload: {
          question: exPayload.question,
          target_collection: exPayload.target_collection,
          target_database: exPayload.target_database || null,
          query_json: queryJson,
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
        payload: { ...rhPayload },
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
        <Form.Item label="类型" required>
          <Select
            aria-label="类型"
            value={entryType}
            onChange={(v) => setEntryType(v as EntryType)}
            options={[
              { value: "terminology", label: "业务术语 (terminology)" },
              { value: "instance_alias", label: "实例别名 (instance_alias)" },
              { value: "rule", label: "查询规则 (rule)" },
              { value: "example", label: "示例查询 (example)" },
              { value: "route_hint", label: "路由偏好 (route_hint)" },
            ]}
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
          <RuleFields value={rulePayload} onChange={setRulePayload} />
        )}

        {entryType === "instance_alias" && defaultNamespaceId !== undefined && (
          <>
            <Form.Item label="别名 (alias)" required>
              <Input
                value={iaPayload.alias}
                onChange={(e) => setIaPayload({ ...iaPayload, alias: e.target.value })}
                placeholder="用户问题里的简称, 如 黄金会员"
                maxLength={50}
              />
            </Form.Item>
            <Form.Item label="全名 (canonical_name)">
              <Input
                value={iaPayload.canonical_name}
                onChange={(e) => setIaPayload({ ...iaPayload, canonical_name: e.target.value })}
                placeholder="记录的全名, 供审核者识别"
              />
            </Form.Item>
            <Form.Item label="目标 database" required>
              <Input
                value={iaPayload.target_database}
                onChange={(e) => setIaPayload({ ...iaPayload, target_database: e.target.value })}
                placeholder="数据库名"
              />
            </Form.Item>
            <Form.Item label="目标 collection" required>
              <Input
                value={iaPayload.target_collection}
                onChange={(e) => setIaPayload({ ...iaPayload, target_collection: e.target.value })}
                placeholder="集合名"
              />
            </Form.Item>
            <Form.Item label="记录 ID (target_id)" required>
              <Input
                value={iaPayload.target_id}
                onChange={(e) => setIaPayload({ ...iaPayload, target_id: e.target.value })}
                placeholder="_id 或唯一键值"
              />
            </Form.Item>
            <Form.Item label="ID 字段名 (id_field)">
              <Input
                value={iaPayload.id_field}
                onChange={(e) => setIaPayload({ ...iaPayload, id_field: e.target.value })}
                placeholder="默认 _id, 自定义唯一键填实际字段名"
              />
            </Form.Item>
          </>
        )}

        {entryType === "example" && (
          <ExampleFields
            value={exPayload} onChange={setExPayload}
            jsonError={jsonError} clearJsonError={() => setJsonError("")}
          />
        )}

        {entryType === "route_hint" && (
          <RouteHintFields value={rhPayload} onChange={setRhPayload} />
        )}
      </Form>
    </Modal>
  );
}

function RuleFields({
  value, onChange,
}: { value: RulePayloadDraft; onChange: (v: RulePayloadDraft) => void }) {
  return (
    <>
      <Form.Item label="rule_text" required>
        <Input.TextArea
          aria-label="rule_text"
          rows={4}
          value={value.rule_text}
          onChange={(e) => onChange({ ...value, rule_text: e.target.value })}
          placeholder="例: 查询订单时, 默认按下单时间倒序"
        />
      </Form.Item>
      <Form.Item label="applies_to_collections (可选, 逗号分隔)">
        <Select
          aria-label="applies_to_collections"
          mode="tags"
          value={value.applies_to_collections}
          onChange={(next: string[]) =>
            onChange({ ...value, applies_to_collections: next })
          }
          tokenSeparators={[",", "，"]}
          notFoundContent={null}
          open={false}
        />
      </Form.Item>
      <Form.Item label="priority (可选, 默认 0)">
        <InputNumber
          aria-label="priority"
          value={value.priority}
          onChange={(n) => onChange({ ...value, priority: n ?? 0 })}
          min={0}
        />
      </Form.Item>
    </>
  );
}

function ExampleFields({
  value, onChange, jsonError, clearJsonError,
}: {
  value: ExamplePayloadDraft;
  onChange: (v: ExamplePayloadDraft) => void;
  jsonError: string;
  clearJsonError: () => void;
}) {
  return (
    <>
      <Form.Item label="question" required>
        <Input.TextArea
          aria-label="question"
          rows={2}
          value={value.question}
          onChange={(e) => onChange({ ...value, question: e.target.value })}
          placeholder="自然语言问题, 例: 上周销售额最高的商品"
        />
      </Form.Item>
      <Form.Item label="target_collection" required>
        <Input
          aria-label="target_collection"
          value={value.target_collection}
          onChange={(e) => onChange({ ...value, target_collection: e.target.value })}
        />
      </Form.Item>
      <Form.Item label="target_database (可选)">
        <Input
          aria-label="target_database"
          value={value.target_database}
          onChange={(e) => onChange({ ...value, target_database: e.target.value })}
        />
      </Form.Item>
      <Form.Item
        label="query_json (合法 JSON)"
        required
        validateStatus={jsonError ? "error" : ""}
        help={jsonError}
      >
        <Input.TextArea
          aria-label="query_json"
          rows={6}
          value={value.query_json_text}
          onChange={(e) => {
            clearJsonError();
            onChange({ ...value, query_json_text: e.target.value });
          }}
          placeholder='{"filter": {"week": "last"}, "sort": {"sales": -1}}'
        />
      </Form.Item>
      <Form.Item label="result_summary (可选)">
        <Input
          aria-label="result_summary"
          value={value.result_summary}
          onChange={(e) => onChange({ ...value, result_summary: e.target.value })}
        />
      </Form.Item>
    </>
  );
}

function RouteHintFields({
  value, onChange,
}: { value: RouteHintPayloadDraft; onChange: (v: RouteHintPayloadDraft) => void }) {
  return (
    <>
      <Form.Item label="question_pattern" required>
        <Input
          aria-label="question_pattern"
          value={value.question_pattern}
          onChange={(e) => onChange({ ...value, question_pattern: e.target.value })}
          placeholder="问题模式, 例: 查 X 关联的 Y"
        />
      </Form.Item>
      <Form.Item label="collection_path (路径序列)" required>
        <Select
          aria-label="collection_path"
          mode="tags"
          value={value.collection_path}
          onChange={(next: string[]) =>
            onChange({ ...value, collection_path: next })
          }
          tokenSeparators={[",", "，", "→"]}
          notFoundContent={null}
          open={false}
          placeholder="按顺序输入: a → b → c, 或逗号分隔"
        />
      </Form.Item>
      <Form.Item label="cost_strategy">
        <Select
          aria-label="cost_strategy"
          value={value.cost_strategy}
          onChange={(v) => onChange({ ...value, cost_strategy: v })}
          options={[
            { value: "default", label: "default" },
            { value: "low", label: "low (走 count_only / 分批)" },
            { value: "high", label: "high (大数据量预警)" },
          ]}
        />
      </Form.Item>
      <Form.Item label="reason (可选)">
        <Input.TextArea
          aria-label="reason"
          rows={2}
          value={value.reason}
          onChange={(e) => onChange({ ...value, reason: e.target.value })}
        />
      </Form.Item>
    </>
  );
}

