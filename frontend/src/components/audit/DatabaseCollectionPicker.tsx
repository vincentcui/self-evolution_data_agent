/* ════════════════════════════════════════════════════════════════════════════
 *  DatabaseCollectionPicker — database→collection 联动可搜索下拉 (公共组件)
 *  ────────────────────────────────────────────────────────────────────────
 *  联动协议 (照搬 TerminologyEditPanel 范式):
 *    1. mount → GET /namespaces/{ns}/databases   一级下拉
 *    2. database 改变 → GET /collections 二级联动 (collectionsByDb 分桶缓存)
 *    3. multiple 模式: 切库不清空已选 (跨库路径), option value 编码 "db::coll"
 *    4. single 模式: 切库重置 collection (同 terminology)
 *    5. 纯下拉强校验: 列表外不可输入 (showSearch 不开 mode=tags)
 * ══════════════════════════════════════════════════════════════════════════ */
import React, { useEffect, useMemo, useState } from "react";
import { Select } from "antd";
import { getCollections, getDatabases, type NamespaceDatabase } from "@/api";
import { DB_TYPE_META, type DbType } from "@/types";
import type { CollectionRef } from "@/types";

export type { CollectionRef } from "@/types";

interface Props {
  nsId: number;
  mode: "single" | "multiple";
  value: CollectionRef[];
  onChange: (v: CollectionRef[]) => void;
  onDbTypeChange?: (dbType: DbType | null) => void;
  disabled?: boolean;
  placeholderDb?: string;
  placeholderColl?: string;
}

const encode = (r: CollectionRef) => `${r.database}::${r.collection}`;
const decode = (s: string): CollectionRef | null => {
  const i = s.indexOf("::");
  if (i < 0) return null;
  return { database: s.slice(0, i), collection: s.slice(i + 2) };
};

export function DatabaseCollectionPicker({
  nsId, mode, value, onChange, onDbTypeChange, disabled,
  placeholderDb = "选择 database", placeholderColl,
}: Props) {
  const [databases, setDatabases] = useState<NamespaceDatabase[]>([]);
  const [collectionsByDb, setCollectionsByDb] = useState<Record<string, string[]>>({});
  const [currentDb, setCurrentDb] = useState<string>("");

  // ── 一级下拉: 加载 namespace 下所有 DataSource ──
  useEffect(() => {
    let alive = true;
    getDatabases(nsId).then((r) => { if (alive) setDatabases(r.databases); }).catch(() => {});
    return () => { alive = false; };
  }, [nsId]);

  // ── 二级下拉: database 变化时拉 collections (分桶缓存, 幂等) ──
  useEffect(() => {
    if (!currentDb) return;
    if (collectionsByDb[currentDb] !== undefined) return;
    let alive = true;
    getCollections(nsId, currentDb).then((r) => {
      if (!alive) return;
      setCollectionsByDb((p) => ({ ...p, [currentDb]: r.collections }));
    }).catch(() => {});
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nsId, currentDb]);

  const currentDbType = useMemo(
    () => databases.find((d) => d.database === currentDb)?.db_type,
    [databases, currentDb],
  );
  const isSql = DB_TYPE_META[currentDbType as keyof typeof DB_TYPE_META]?.isSql;
  const collLabel = !isSql ? "集合" : "表";
  const collPlaceholder = placeholderColl ?? `选择${collLabel}`;

  const handleDbChange = (db: string) => {
    setCurrentDb(db);
    const dbType = databases.find((d) => d.database === db)?.db_type ?? null;
    onDbTypeChange?.(dbType);
    if (mode === "single") onChange([]);  // single 切库重置
    // multiple 不清空: 已选跨库保留
  };

  const handleCollChange = (next: string | string[]) => {
    // antd: multiple → string[], single → string
    const arr = Array.isArray(next) ? next : [next];
    onChange(arr.map(decode).filter(Boolean) as CollectionRef[]);
  };

  const selectedValues = mode === "multiple"
    ? value.map(encode)
    : value.length > 0 ? encode(value[0]) : undefined;

  const previousSelectedOptions = mode === "multiple"
    ? value
        .filter((r) => r.database !== currentDb)
        .map((r) => ({ label: `${r.database}.${r.collection}`, value: encode(r) }))
    : [];
  const currentOptions = [
    ...previousSelectedOptions,
    ...(collectionsByDb[currentDb] ?? []).map((c) => ({ label: c, value: `${currentDb}::${c}` })),
  ];

  return (
    <>
      <Select
        aria-label="数据库"
        showSearch
        value={currentDb || undefined}
        placeholder={placeholderDb}
        onChange={handleDbChange}
        disabled={disabled}
        options={databases.map((d) => ({ label: `${d.database} (${d.db_type})`, value: d.database }))}
        style={{ width: 260, marginBottom: 8 }}
      />
      <Select
        aria-label={collLabel}
        showSearch
        mode={mode === "multiple" ? "multiple" : undefined}
        value={selectedValues}
        onChange={handleCollChange}
        disabled={disabled || !currentDb}
        options={currentOptions}
        placeholder={collPlaceholder}
        style={{ width: "100%" }}
        tagRender={mode === "multiple" ? (p) => {
          if (typeof p.value !== "string") return <>{p.label}</>;
          const r = decode(p.value);
          return <span style={{ marginRight: 4 }}>{r ? `${r.database}.${r.collection}` : p.value}</span>;
        } : undefined}
      />
    </>
  );
}
