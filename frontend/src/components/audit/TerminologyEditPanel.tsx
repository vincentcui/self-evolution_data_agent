/* ════════════════════════════════════════════════════════════════════════════
 *  TerminologyEditPanel — terminology 类型 KE 的结构化编辑面板
 *  ────────────────────────────────────────────────────────────────────────
 *  联动协议:
 *    1. mount → GET /namespaces/{ns}/databases   一级下拉数据源
 *    2. primary_database 改变 → GET /collections 二级联动 + 同步 db_type
 *    3. db_type 始终 readOnly — 由后端 DataSource 真相源决定
 *    4. synonyms: antd Select tags 模式 (回车 / 逗号自动转 tag)
 *
 *  Strict-mode 安全: 用 alive flag 防 race, 不用 ref guard (ref guard 在
 *  React 18 双重 mount 下会把第一次 fire 的响应丢掉, 导致 collections 永远空).
 * ══════════════════════════════════════════════════════════════════════════ */

import React, { useEffect, useState } from "react";
import { Form, Input, Select } from "antd";
import { getCollections, getDatabases, type NamespaceDatabase } from "@/api";
import type { DbType } from "@/types";
import { DB_TYPE_META } from "@/types";

export interface TerminologyPayload {
  term?: string;
  primary_collection?: string;
  primary_database?: string;
  db_type?: DbType;
  synonyms?: string[];
  source_collections?: string[];
}

interface Props {
  nsId: number;
  value: TerminologyPayload;
  onChange: (next: TerminologyPayload) => void;
  termError?: string;
  onTermBlur?: (term: string) => void;
  /**
   * 锁定路由三件套 (primary_database / primary_collection / db_type) 不可改.
   * manual_edit 冲突解决时启用 — 只允许改 term/synonyms, 不允许跨表迁移.
   */
  lockRouting?: boolean;
}

export default function TerminologyEditPanel({
  nsId,
  value,
  onChange,
  termError,
  onTermBlur,
  lockRouting = false,
}: Props) {
  const [databases, setDatabases] = useState<NamespaceDatabase[]>([]);
  // ── collections 缓存按 db 名分桶, 第一笔响应填进来不会被第二次 effect 误清空 ──
  const [collectionsByDb, setCollectionsByDb] = useState<Record<string, string[]>>({});

  // ── 一级下拉: 加载 namespace 下所有 DataSource ──
  useEffect(() => {
    let alive = true;
    getDatabases(nsId)
      .then((r) => { if (alive) setDatabases(r.databases); })
      .catch(() => { /* silent — 网络故障不阻塞表单 */ });
    return () => { alive = false; };
  }, [nsId]);

  // ── 二级下拉: primary_database 变化时拉 collections + 同步 db_type ──
  //    幂等保护: 命中已缓存的 db 直接跳过 fetch (二次 mount race 也不重复打)
  const currentDb = value.primary_database;
  useEffect(() => {
    if (!currentDb) return;
    if (collectionsByDb[currentDb] !== undefined) return;  // 已缓存

    let alive = true;
    getCollections(nsId, currentDb)
      .then((r) => {
        if (!alive) return;
        setCollectionsByDb((prev) => ({ ...prev, [currentDb]: r.collections }));
        // ── db_type 真相源同步 (避免无限循环: 仅 differ 时 onChange) ──
        if (r.db_type && r.db_type !== value.db_type) {
          onChange({ ...value, db_type: r.db_type });
        }
      })
      .catch(() => { /* silent */ });
    return () => { alive = false; };
    // 依赖 nsId + currentDb 即可; value/onChange 故意忽略避免循环
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nsId, currentDb]);

  const collections = currentDb ? (collectionsByDb[currentDb] ?? []) : [];

  // ── label 按 db_type 的 isSql 区分: document 用"集合/collection", relational 用"表/table" ──
  const collectionLabel =
    !DB_TYPE_META[value.db_type as keyof typeof DB_TYPE_META]?.isSql ? "集合" : "表";
  const collectionPlaceholder =
    !DB_TYPE_META[value.db_type as keyof typeof DB_TYPE_META]?.isSql ? "选择集合" : "选择表";

  const handleDatabaseChange = (db: string) => {
    // ── primary_database 切换 → 强制重置 collection (避免脏数据) ──
    onChange({
      ...value,
      primary_database: db,
      primary_collection: "",
    });
  };

  return (
    <>
      <Form.Item
        label="术语"
        validateStatus={termError ? "error" : ""}
        help={termError}
      >
        <Input
          aria-label="术语"
          value={value.term ?? ""}
          placeholder="单一业务名词 (≤20 字)"
          onChange={(e) => onChange({ ...value, term: e.target.value })}
          onBlur={(e) => onTermBlur?.(e.target.value)}
        />
      </Form.Item>

      <Form.Item label="数据库">
        <Select
          aria-label="数据库"
          value={value.primary_database || undefined}
          placeholder="选择 database"
          onChange={handleDatabaseChange}
          disabled={lockRouting}
          options={databases.map((d) => ({
            label: `${d.database} (${d.db_type})`,
            value: d.database,
          }))}
          style={{ width: 320 }}
        />
      </Form.Item>

      <Form.Item label="数据库类型">
        <Input
          aria-label="数据库类型"
          value={value.db_type ?? ""}
          disabled
          placeholder="自动同步"
          style={{ width: 200 }}
        />
      </Form.Item>

      <Form.Item label={collectionLabel}>
        <Select
          aria-label="集合/表"
          value={value.primary_collection || undefined}
          placeholder={collectionPlaceholder}
          onChange={(c) => onChange({ ...value, primary_collection: c })}
          disabled={lockRouting || !value.primary_database}
          options={collections.map((c) => ({ label: c, value: c }))}
          style={{ width: 320 }}
        />
      </Form.Item>

      <Form.Item label="同义词">
        {/* antd Select tags 模式 — 回车 / 逗号自动转 tag, 比纯 Input 体验好且无逗号被吞 bug */}
        <Select
          aria-label="同义词"
          mode="tags"
          value={value.synonyms ?? []}
          onChange={(next: string[]) =>
            onChange({
              ...value,
              synonyms: next.map((s) => s.trim()).filter(Boolean),
            })
          }
          tokenSeparators={[",", "，"]}
          placeholder="按回车确认; 也可用逗号 / 中文逗号分隔; 例: 品名, 货品"
          style={{ width: "100%" }}
          notFoundContent={null}
          open={false}
        />
      </Form.Item>
    </>
  );
}
