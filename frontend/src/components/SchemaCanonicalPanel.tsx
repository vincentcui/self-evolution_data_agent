/**
 * SchemaCanonicalPanel — 6 子 Tab 容器
 *
 * Tabs: 全部 | 枚举字典 | 待汇聚 | 待确认 | 冲突 | 审计
 * 每个 Tab 顶部 Badge 显示计数 (从 pending-counts API 拉取)
 */
import React, { useEffect, useRef, useState } from "react";
import { Badge, Button, message, Select, Space, Tabs } from "antd";
import { ReloadOutlined } from "@ant-design/icons";

import { schemaCanonicalApi, terminologyApi } from "@/api";
import { DB_TYPE_META, type DbType } from "@/types";
import type {
  PendingCounts,
  SchemaCanonicalField,
  SchemaCanonicalObject,
  SchemaCanonicalRelationship,
} from "@/types/schema-canonical";
import { AllFieldsTab } from "./schema/AllFieldsTab";
import { PendingPromoteTab } from "./schema/PendingPromoteTab";
import { EvidenceOnlyTab } from "./schema/EvidenceOnlyTab";
import { ConflictsTab } from "./schema/ConflictsTab";
import { SchemaAuditTab } from "./schema/SchemaAuditTab";
import { EnumDictionaryTab } from "./schema/EnumDictionaryTab";
import { EvidenceDrawer } from "./schema/EvidenceDrawer";

import axios from "axios";

const http = axios.create({ baseURL: "/api", timeout: 60_000 });
http.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

const VALID_TAB_KEYS = new Set(["all", "enum", "pending", "evidence", "conflicts", "audit"]);

interface Props {
  namespaceId: number;
}

export const SchemaCanonicalPanel: React.FC<Props> = ({ namespaceId }) => {
  const [scos, setScos] = useState<SchemaCanonicalObject[]>([]);
  const [counts, setCounts] = useState<PendingCounts | null>(null);
  const [activeKey, setActiveKey] = useState("all");
  const [selectedSco, setSelectedSco] = useState<SchemaCanonicalObject | null>(null);
  const [loading, setLoading] = useState(false);
  const [dbType, setDbType] = useState<"all" | DbType>("all");
  const [refreshTrigger, setRefreshTrigger] = useState(0);

  // Evidence drawer state
  const [evidenceDrawer, setEvidenceDrawer] = useState<{
    scoId: number;
    fieldPath: string;
  } | null>(null);

  // Audit filter state (for history button)
  const [auditFilter, setAuditFilter] = useState<{
    scoId: number;
    fieldPath: string;
  } | null>(null);

  // Track selected SCO id to preserve across reloads
  const selectedIdRef = useRef<number | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const params = dbType === "all" ? undefined : { db_type: dbType };
      const [list, c] = await Promise.all([
        schemaCanonicalApi.listCanonicals(namespaceId, params),
        schemaCanonicalApi.getPendingCounts(namespaceId),
      ]);
      setScos(list);
      setCounts(c);

      // Preserve selected SCO if it still exists in the new list
      const prevId = selectedIdRef.current;
      const preserved = prevId != null ? list.find((s) => s.id === prevId) : null;
      if (preserved) {
        setSelectedSco(preserved);
      } else if (list.length > 0) {
        setSelectedSco(list[0]);
        selectedIdRef.current = list[0].id;
      } else {
        setSelectedSco(null);
        selectedIdRef.current = null;
      }
    } catch {
      message.error("加载 Schema Canonical 失败");
    } finally {
      setLoading(false);
    }
  };

  // 仅刷新各 Tab 计数 (不重载列表 / 不打扰当前选中 SCO)
  const refreshCounts = async () => {
    try {
      const c = await schemaCanonicalApi.getPendingCounts(namespaceId);
      setCounts(c);
    } catch {
      // 计数刷新失败不阻塞主流程
    }
  };

  useEffect(() => {
    if (namespaceId) void load();
  }, [namespaceId, dbType]);

  const handleRefreshTerminology = async () => {
    const confirmed = window.confirm(
      "将清除所有自动生成的术语并重新提取，手动录入的术语不受影响。确认继续？",
    );
    if (!confirmed) return;

    setLoading(true);
    try {
      const { task_id, status } = await terminologyApi.refresh(namespaceId);
      if (status === "already_running") {
        message.info("术语提取正在进行中");
        setLoading(false);
        return;
      }
      // 轮询进度
      const poll = setInterval(async () => {
        try {
          const progress = await terminologyApi.getRefreshProgress(namespaceId, task_id);
          if (progress.status === "completed") {
            clearInterval(poll);
            message.success(progress.message);
            setLoading(false);
          } else if (progress.status === "failed") {
            clearInterval(poll);
            message.error(progress.message);
            setLoading(false);
          }
        } catch {
          clearInterval(poll);
          setLoading(false);
        }
      }, 2000);
    } catch {
      message.error("术语提取启动失败");
      setLoading(false);
    }
  };

  const handleDeleteSco = async () => {
    if (!selectedSco || !namespaceId) return;
    try {
      await schemaCanonicalApi.deleteCanonical(namespaceId, selectedSco.id);
      message.success("已删除");
      selectedIdRef.current = null;
      await load();
      setRefreshTrigger((n) => n + 1);
    } catch (e) {
      console.error("delete canonical failed", e);
      message.error("删除失败");
    }
  };

  const handleLockField = async (fieldName: string, locked: boolean) => {
    if (!selectedSco) return;
    try {
      if (locked) {
        await schemaCanonicalApi.lock(namespaceId, selectedSco.id, { field_path: fieldName });
      } else {
        await schemaCanonicalApi.unlock(namespaceId, selectedSco.id, { field_path: fieldName });
      }
      message.success(locked ? "已锁定" : "已解锁");
      void load();
    } catch {
      message.error("操作失败");
    }
  };

  const handleSave = async (payload: {
    description?: string;
    purpose_detail?: string;
	    relationships?: SchemaCanonicalRelationship[];
    fields?: SchemaCanonicalField[];
  }) => {
    if (!selectedSco) return;
    try {
      await http.patch(
        `/namespaces/${namespaceId}/schema-canonical/${selectedSco.id}`,
        payload,
      );
      message.success("保存成功");
      void load();
    } catch {
      message.error("保存失败");
      throw new Error("save failed");
    }
  };

  const handleSelectSco = (id: number) => {
    const sco = scos.find((s) => s.id === id) ?? null;
    setSelectedSco(sco);
    selectedIdRef.current = id;
  };

  const handleOpenEvidence = (fieldName: string) => {
    if (!selectedSco) return;
    setEvidenceDrawer({ scoId: selectedSco.id, fieldPath: fieldName });
  };

  const handleOpenHistory = (fieldName: string) => {
    if (!selectedSco) return;
    setAuditFilter({ scoId: selectedSco.id, fieldPath: fieldName });
    setActiveKey("audit");
  };

  const handleTabChange = (key: string) => {
    // Guard against stale tab keys (e.g., "pending_enum" from old state)
    setActiveKey(VALID_TAB_KEYS.has(key) ? key : "all");
  };

  const items = [
    {
      key: "all",
      label: (
        <span>
          全部 <Badge count={scos.length} showZero style={{ marginLeft: 4 }} />
        </span>
      ),
      children: (
        <Space direction="vertical" style={{ width: "100%" }}>
          <Select
            style={{ width: 420 }}
            value={selectedSco?.id}
            onChange={handleSelectSco}
            showSearch
            optionFilterProp="label"
            options={scos.map((s) => ({
              value: s.id,
              label: `[${s.db_type}] ${s.database} / ${s.target}`,
            }))}
            placeholder="选择表/集合"
          />
          {selectedSco && (
            <AllFieldsTab
              key={selectedSco.id}
              sco={selectedSco}
              namespaceId={namespaceId}
              onOpenEvidence={handleOpenEvidence}
              onOpenHistory={handleOpenHistory}
              onLockField={handleLockField}
              onSave={handleSave}
              onRefresh={load}
              onDelete={handleDeleteSco}
              tableLabel={`[${selectedSco.db_type}] ${selectedSco.database}/${selectedSco.target}`}
            />
          )}
        </Space>
      ),
    },
    {
      key: "enum",
      label: "枚举字典",
      children: (
        <EnumDictionaryTab
          namespaceId={namespaceId}
          dbType={dbType === "all" ? (Object.keys(DB_TYPE_META)[0] as DbType) : dbType}
        />
      ),
    },
    {
      key: "pending",
      label: (
        <span>
          待汇聚{" "}
          <Badge count={counts?.pending_promote ?? 0} style={{ marginLeft: 4 }} />
        </span>
      ),
      children: <PendingPromoteTab namespaceId={namespaceId} key={`pending-${refreshTrigger}`} />,
    },
    {
      key: "evidence",
      label: (
        <span>
          待确认{" "}
          <Badge count={counts?.evidence_only ?? 0} style={{ marginLeft: 4 }} />
        </span>
      ),
      children: <EvidenceOnlyTab namespaceId={namespaceId} key={`evidence-${refreshTrigger}`} />,
    },
    {
      key: "conflicts",
      label: (
        <span>
          冲突 <Badge count={counts?.conflicts ?? 0} style={{ marginLeft: 4 }} />
        </span>
      ),
      children: <ConflictsTab namespaceId={namespaceId} onResolved={refreshCounts} key={`conflicts-${refreshTrigger}`} />,
    },
    {
      key: "audit",
      label: "审计",
      children: (
        <SchemaAuditTab
          namespaceId={namespaceId}
          scoId={auditFilter?.scoId}
          fieldPath={auditFilter?.fieldPath}
          onClearFilter={() => setAuditFilter(null)}
        />
      ),
    },
  ];

  return (
    <div>
      <div style={{ marginBottom: 16, display: "flex", justifyContent: "space-between" }}>
        <Space>
          <span style={{ fontSize: 16, fontWeight: 500 }}>
            Schema 校对 ({scos.length} 个对象)
          </span>
          <Select
            aria-label="数据源类型"
            value={dbType}
            onChange={setDbType}
            options={[
              { value: "all", label: "全部" },
              ...Object.entries(DB_TYPE_META).map(([key, meta]) => ({
                value: key,
                label: meta.label,
              })),
            ]}
            style={{ width: 140 }}
          />
        </Space>
        <Button icon={<ReloadOutlined />} onClick={handleRefreshTerminology} loading={loading}>
          重新提取术语
        </Button>
      </div>
      <Tabs activeKey={activeKey} onChange={handleTabChange} items={items} />

      {/* Evidence Drawer */}
      {evidenceDrawer && (
        <EvidenceDrawer
          namespaceId={namespaceId}
          scoId={evidenceDrawer.scoId}
          fieldPath={evidenceDrawer.fieldPath}
          open={true}
          onClose={() => {
            setEvidenceDrawer(null);
            void load();
          }}
        />
      )}
    </div>
  );
};
