/* ════════════════════════════════════════════════════════════════════════════
 *  RouteHintEditPanel — route_hint 类型 KE 的结构化编辑面板 (手动录入, spec 2026-07-08)
 *  ────────────────────────────────────────────────────────────────────────
 *  route_hint 无自动抽取, deliberate 录入: 所有字段可编. question_pattern 不在 payload (唯一真相源
 *  是 entry.content, 由 EditCanonicalForm 承载). payload = {collection_path, navigation_note}.
 * ════════════════════════════════════════════════════════════════════════════ */

import React from "react";
import { Form, Input } from "antd";
import type { CollectionRef } from "@/types";
import { DatabaseCollectionPicker } from "./DatabaseCollectionPicker";

export interface RouteHintPayload {
  collection_path: CollectionRef[];
  navigation_note: string;
}

interface Props {
  nsId: number;
  value: RouteHintPayload;
  onChange: (next: RouteHintPayload) => void;
}

export default function RouteHintEditPanel({ nsId, value, onChange }: Props) {
  const update = (patch: Partial<RouteHintPayload>) =>
    onChange({ ...value, ...patch });

  return (
    <>
      <Form.Item label="集合路径 (有序)">
        <DatabaseCollectionPicker
          nsId={nsId}
          mode="multiple"
          value={value.collection_path}
          onChange={(cp) => update({ collection_path: cp })}
        />
      </Form.Item>

      <Form.Item label="导航说明 (关联字段 / 关联类型 / 嵌套位置 / 避坑)">
        <Input.TextArea
          aria-label="导航说明"
          value={value.navigation_note}
          onChange={(e) => update({ navigation_note: e.target.value })}
          autoSize={{ minRows: 2, maxRows: 6 }}
          placeholder="orders.items[].sku ↔ products.sku (nested_array, 非 products.id); 类别在 products.categories[] 需 $unwind"
        />
      </Form.Item>
    </>
  );
}
