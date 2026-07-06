/* ════════════════════════════════════════════════════════════════════════════
 *  RouteHintEditPanel — route_hint 类型 KE 的结构化编辑面板
 *  ────────────────────────────────────────────────────────────────────────
 *  机械字段 (collection_path / join_fields / cost_strategy) 来自 trace, 只读;
 *  仅 reason (≤30 字, 路径选择理由) 允许审核者编辑.
 * ══════════════════════════════════════════════════════════════════════════ */

import React from "react";
import { Form, Input, Tag, Typography } from "antd";

const { Text } = Typography;

// 路径理由长度上限 — UX 约束, 后端不强校验, 防止审核者写出长篇说明把字段当 description 用
const REASON_MAX_LEN = 30;

export interface RouteHintPayload {
  collection_path: string[];
  join_fields: { a: string; b: string }[];
  cost_strategy: string;
  reason: string;
}

interface Props {
  value: RouteHintPayload;
  onChange: (next: RouteHintPayload) => void;
}

export default function RouteHintEditPanel({ value, onChange }: Props) {
  const update = (patch: Partial<RouteHintPayload>) =>
    onChange({ ...value, ...patch });

  return (
    <>
      <Form.Item label="集合路径 (只读, 来自执行记录)">
        <div>
          {value.collection_path.map((c, i, arr) => (
            <span key={c}>
              <Tag color="cyan">{c}</Tag>
              {i < arr.length - 1 && <Text type="secondary"> → </Text>}
            </span>
          ))}
        </div>
      </Form.Item>

      <Form.Item label="连接字段 (只读, 来自执行记录)">
        <div>
          {value.join_fields.length === 0 ? (
            <Tag>无</Tag>
          ) : (
            value.join_fields.map((j, i) => (
              <Tag key={`${j.a}:${j.b}:${i}`}>{j.a} ↔ {j.b}</Tag>
            ))
          )}
        </div>
      </Form.Item>

      <Form.Item label="成本策略 (只读, 来自执行记录)">
        <Tag>{value.cost_strategy}</Tag>
      </Form.Item>

      <Form.Item label={`路径理由 (reason, ≤${REASON_MAX_LEN} 字)`}>
        <Input
          aria-label="原因"
          value={value.reason}
          onChange={(e) => update({ reason: e.target.value })}
          maxLength={REASON_MAX_LEN}
          showCount
        />
      </Form.Item>
    </>
  );
}
