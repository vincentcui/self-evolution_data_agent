/* ════════════════════════════════════════════════════════════════════════════
 *  ExampleEditPanel — example 类型 KE 的结构化编辑面板
 *  ────────────────────────────────────────────────────────────────────────
 *  对齐后端 ExamplePayload schema (knowledge_payload.py):
 *    question_pattern / collections / join_keys / final_query_plan / result_summary
 *
 *  可编辑: question_pattern + result_summary
 *  只读:   collections / join_keys / final_query_plan (来自执行结果)
 * ══════════════════════════════════════════════════════════════════════════ */

import React from "react";
import { Form, Input, Space, Tag, Typography } from "antd";

const { Text } = Typography;

export interface ExamplePayload {
  question_pattern: string;
  collections: string[];
  join_keys: Record<string, unknown>[];
  final_query_plan: Record<string, unknown> | null;
  result_summary: string;
  // Legacy compat — read for fallback, not editable
  question?: string;
  target_collection?: string;
  query_json?: Record<string, unknown> | null;
  [key: string]: unknown;
}

interface Props {
  value: ExamplePayload;
  onChange: (next: ExamplePayload) => void;
}

export default function ExampleEditPanel({ value, onChange }: Props) {
  const questionPattern = value.question_pattern || value.question || "";

  const update = (patch: Partial<ExamplePayload>) =>
    onChange({ ...value, ...patch });

  return (
    <>
      <Form.Item label="问题模式">
        <Input.TextArea
          aria-label="问题模式"
          value={questionPattern}
          onChange={(e) => update({ question_pattern: e.target.value })}
          rows={2}
        />
      </Form.Item>

      <Form.Item label="结果摘要">
        <Input.TextArea
          aria-label="结果摘要"
          value={value.result_summary}
          onChange={(e) => update({ result_summary: e.target.value })}
          rows={2}
          maxLength={120}
          showCount
        />
      </Form.Item>

      <Form.Item label="涉及集合 (只读)">
        <Space wrap>
          {(value.collections || []).map((c) => (
            <Tag key={c} color="blue">{c}</Tag>
          ))}
          {(!value.collections || value.collections.length === 0) && (
            <Text type="secondary">(空)</Text>
          )}
        </Space>
      </Form.Item>

      <Form.Item label="连接键 (只读)">
        {value.join_keys && value.join_keys.length > 0 ? (
          <Space direction="vertical">
            {value.join_keys.map((jk, i) => (
              <Text key={i} code>
                {jk.from as string} → {jk.to as string}
              </Text>
            ))}
          </Space>
        ) : (
          <Text type="secondary">(空)</Text>
        )}
      </Form.Item>

      <Form.Item label="查询计划 (只读)">
        <Input.TextArea
          aria-label="查询计划"
          value={value.final_query_plan
            ? JSON.stringify(value.final_query_plan, null, 2)
            : (value.query_json ? JSON.stringify(value.query_json, null, 2) : "")}
          readOnly
          rows={8}
          style={{ fontFamily: "monospace", fontSize: 11 }}
        />
      </Form.Item>
    </>
  );
}
