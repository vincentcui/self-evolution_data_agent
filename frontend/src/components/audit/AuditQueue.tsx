/* ════════════════════════════════════════════
 *  AuditQueue — 待审 / 已通过 / 已拒绝队列容器
 * ════════════════════════════════════════════ */

import React, { useEffect, useState } from "react";
import { Card, Empty, Input, Pagination, Select, Space, Spin, Tag } from "antd";
import { fetchAuditQueue, type AuditQueueOut } from "@/api";
import AuditCard from "./AuditCard";
import BatchAuditBar from "./BatchAuditBar";

type StatusValue = "proposed" | "canonical" | "rejected" | "superseded";

interface Props {
  nsId: number | undefined;
  /** 锁定 status — 三 tab 中 audit-pending / audit-rejected 用. 与 showStatusFilter 互斥. */
  status?: StatusValue;
  onChange?: () => void;
  /** 知识条目 tab 用 — 露出 status 下拉, 默认全量 status. props.status 已锁定时该 prop 失效. */
  showStatusFilter?: boolean;
}

const PAGE_SIZE = 20;
const SEARCH_DEBOUNCE_MS = 300;

const STATUS_OPTIONS: { label: string; value: StatusValue }[] = [
  { label: "待审", value: "proposed" },
  { label: "已通过", value: "canonical" },
  { label: "已拒绝", value: "rejected" },
  { label: "已替代", value: "superseded" },
];

export default function AuditQueue({
  nsId, status, onChange, showStatusFilter = false,
}: Props) {
  const [data, setData] = useState<AuditQueueOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [entryType, setEntryType] = useState<string | undefined>();
  const [source, setSource] = useState<string | undefined>();
  const [keyword, setKeyword] = useState("");
  const [debouncedKw, setDebouncedKw] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusValue | undefined>();
  const [selected, setSelected] = useState<Set<number>>(new Set());

  /* keyword → debouncedKw (300ms), 同时重置 page=1 防越界 */
  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedKw(keyword);
      setPage(1);
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [keyword]);

  /** 实际生效 status: props.status 锁定优先, 其次 showStatusFilter 内部 state, 否则 undefined */
  const effectiveStatus = status ?? (showStatusFilter ? statusFilter : undefined);

  const reload = async () => {
    setLoading(true);
    try {
      const params: Parameters<typeof fetchAuditQueue>[0] = {
        namespace_id: nsId,
        entry_type: entryType,
        source,
        page, size: PAGE_SIZE,
      };
      if (debouncedKw) params.q = debouncedKw;
      if (effectiveStatus) params.status = effectiveStatus;
      const r = await fetchAuditQueue(params);
      setData(r);
      setSelected(new Set());
    } finally { setLoading(false); }
  };

  useEffect(() => { reload(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ },
    [nsId, status, entryType, source, debouncedKw, statusFilter, page]);

  const onAfterAction = () => { reload(); onChange?.(); };

  const showLockedStatusTag = !!status;

  return (
    <Card>
      <Space style={{ marginBottom: 16 }} wrap>
        <Input.Search
          allowClear
          placeholder="搜索 content/description/payload"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          style={{ width: 260 }}
        />
        <Select placeholder="类型" allowClear value={entryType} onChange={setEntryType}
          options={[
            { label: "业务术语", value: "terminology" },
            { label: "实例别名", value: "instance_alias" },
            { label: "示例查询", value: "example" },
            { label: "查询规则", value: "rule" },
            { label: "路由偏好", value: "route_hint" },
          ]} style={{ width: 120 }} />
        <Select placeholder="来源" allowClear value={source} onChange={setSource}
          options={[
            { label: "Schema 抽取", value: "schema" },
            { label: "手动", value: "manual" },
            { label: "Agent 学习", value: "agent_learn" },
            { label: "代码提取", value: "code_extract" },
          ]} style={{ width: 130 }} />
        {showStatusFilter && !status && (
          <Select placeholder="状态" allowClear value={statusFilter} onChange={setStatusFilter}
            options={STATUS_OPTIONS} style={{ width: 140 }} />
        )}
        {showLockedStatusTag && (
          <Tag color="blue">
            {STATUS_OPTIONS.find((o) => o.value === status)?.label ?? status}
          </Tag>
        )}
        <Tag>共 {data?.total ?? 0} 条</Tag>
      </Space>

      {selected.size > 0 && (
        <BatchAuditBar
          entryIds={Array.from(selected)}
          onDone={onAfterAction}
        />
      )}

      <Spin spinning={loading}>
        {data && data.items.length === 0 ? <Empty /> : (
          <Space direction="vertical" style={{ width: "100%" }}>
            {data?.items.map((entry) => (
              <AuditCard
                key={entry.id} entry={entry}
                selectable={status === "proposed"}
                selected={selected.has(entry.id)}
                onSelect={(s) => {
                  const next = new Set(selected);
                  if (s) next.add(entry.id); else next.delete(entry.id);
                  setSelected(next);
                }}
                onAction={onAfterAction}
              />
            ))}
          </Space>
        )}
      </Spin>

      <Pagination
        current={page} pageSize={PAGE_SIZE} total={data?.total ?? 0}
        onChange={setPage} style={{ marginTop: 16, textAlign: "right" }}
        showSizeChanger={false}
      />
    </Card>
  );
}
