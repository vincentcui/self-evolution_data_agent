/* ════════════════════════════════════════════════════════════════════════════
 *  ExampleEditPanel — example 类型 KE 的结构化编辑面板
 *  ────────────────────────────────────────────────────────────────────────
 *  对齐后端 ExamplePayload schema (knowledge_payload.py):
 *    question / target_collection / target_database / query_json / result_summary
 *
 *  可编辑: question + result_summary
 *  只读:   target_collection / target_database / query_json (来自执行结果)
 * ══════════════════════════════════════════════════════════════════════════ */

import React from "react";
import { Form, Input, Tag, Typography } from "antd";

const { Text } = Typography;

export interface ExamplePayload {
  question: string;
  target_collection: string;
  target_database: string | null;
  query_json: Record<string, unknown> | null;
  result_summary: string;
  // 透传字段 (不展示编辑控件, 提交时原样回传)
  [key: string]: unknown;
}

interface Props {
  value: ExamplePayload;
  onChange: (next: ExamplePayload) => void;
}

export default function ExampleEditPanel({ value, onChange }: Props) {
  const update = (patch: Partial<ExamplePayload>) =>
    onChange({ ...value, ...patch });

  return (
    <>
      <Form.Item label="问题 (question)">
        <Input.TextArea
          aria-label="question"
          value={value.question}
          onChange={(e) => update({ question: e.target.value })}
          rows={2}
        />
      </Form.Item>

      <Form.Item label="结果摘要 (result_summary)">
        <Input.TextArea
          aria-label="result_summary"
          value={value.result_summary}
          onChange={(e) => update({ result_summary: e.target.value })}
          rows={2}
          maxLength={120}
          showCount
        />
      </Form.Item>

      <Form.Item label="目标集合 (target_collection, 只读)">
        <Tag color="blue">{value.target_collection || "(空)"}</Tag>
      </Form.Item>

      <Form.Item label="目标数据库 (target_database, 只读)">
        <Text>{value.target_database || "(空)"}</Text>
      </Form.Item>

      <Form.Item label="查询 JSON (query_json, 只读)">
        <Input.TextArea
          aria-label="query_json"
          value={value.query_json ? JSON.stringify(value.query_json, null, 2) : ""}
          readOnly
          rows={8}
          style={{ fontFamily: "monospace", fontSize: 11 }}
        />
      </Form.Item>
    </>
  );
}
