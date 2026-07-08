/* ════════════════════════════════════════════
 *  数据源管理页 — 数据源卡片列表 (当前空间取自 WorkspacePage 共享 context)
 *
 *  拆分自原 NamespacePage.tsx: 数据源 Tab 内容独立成页, 与 Git 仓库页
 *  分属左侧菜单两项。删除空间按钮已迁移到工作台首页"我的空间"卡片。
 * ════════════════════════════════════════════ */

import React, { useCallback, useEffect, useState } from "react";
import { Button, Popconfirm, Tag, message } from "antd";
import { DeleteOutlined, PlusOutlined } from "@ant-design/icons";
import { useOutletContext } from "react-router-dom";
import * as api from "@/api";
import DataSourceFormModal from "@/components/DataSourceFormModal";
import type { WorkspaceOutletContext } from "@/components/WorkspacePage";
import type { DataSource } from "@/types";
import { DB_TYPE_META } from "@/types";
import styles from "@/styles/namespace.module.css";
import globalStyles from "@/styles/global.module.css";

const DataSourcePage: React.FC = () => {
  const { activeNs } = useOutletContext<WorkspaceOutletContext>();
  const [showDsModal, setShowDsModal] = useState(false);
  const [datasources, setDatasources] = useState<DataSource[]>([]);
  const [refreshingDs, setRefreshingDs] = useState<number | null>(null);

  const loadDetail = useCallback(async () => {
    if (!activeNs) return;
    const ds = await api.fetchDataSources(activeNs.id);
    setDatasources(ds);
  }, [activeNs]);

  useEffect(() => {
    loadDetail();
  }, [loadDetail]);

  const handleRefreshSchema = async (dsId: number) => {
    if (!activeNs) return;
    setRefreshingDs(dsId);
    try {
      const result = await api.refreshSchema(activeNs.id, dsId);
      message.success(result.message);
    } catch {
      message.error("Schema 刷新请求失败");
    } finally {
      setRefreshingDs(null);
    }
  };

  const handleDeleteDs = async (dsId: number) => {
    if (!activeNs) return;
    await api.deleteDataSource(activeNs.id, dsId);
    message.success("数据源已删除");
    loadDetail();
  };

  return (
    <div>
      <div className={globalStyles.pageHeader}>
        <div>
          <h1 className={globalStyles.pageTitle}>数据源</h1>
          <p className={globalStyles.pageSubtitle}>
            {activeNs ? `当前空间: ${activeNs.name}` : "管理当前空间的数据库连接"}
          </p>
        </div>
      </div>

      <div className={styles.container}>
        {activeNs ? (
          <div className={styles.detailPanel}>
            <div className={styles.detailContent}>
              <Button
                size="small"
                type="default"
                icon={<PlusOutlined />}
                onClick={() => setShowDsModal(true)}
                style={{
                  marginBottom: 12,
                  background: "#eff6ff",
                  borderColor: "#dbeafe",
                  color: "#2563eb",
                }}
              >
                添加数据源
              </Button>
              {datasources.map((ds) => {
                const profiledAt = ds.db_profile?.profiled_at as string | undefined;
                const version = ds.db_profile?.version as string | undefined;
                const objCount = ds.db_profile?.object_count as number | undefined;
                return (
                <div key={ds.id} className={styles.dsCard} data-testid="ds-card">
                  <div className={styles.dsInfo}>
                    <div className={styles.dsIcon}>
                      {(DB_TYPE_META[ds.db_type] ?? DB_TYPE_META.mysql).short}
                    </div>
                    <div>
                      <div className={styles.dsName}>{ds.database}</div>
                      <div className={styles.dsMeta}>
                        {ds.host}:{ds.port} · {ds.db_type.toUpperCase()}
                        {version ? ` · v${version}` : ""}
                        {typeof objCount === "number" ? ` · ${objCount} 对象` : ""}
                        {ds.timezone ? ` · ${ds.timezone}` : ""}
                      </div>
                      {ds.description ? (
                        <div className={styles.dsMeta}>{ds.description}</div>
                      ) : null}
                    </div>
                  </div>
                  <div className={styles.dsActions}>
                    <Tag color={profiledAt ? "success" : "default"}>
                      {profiledAt
                        ? `初始连接于 ${profiledAt.slice(0, 16).replace("T", " ")}`
                        : "已添加"}
                    </Tag>
                    {(DB_TYPE_META[ds.db_type] ?? DB_TYPE_META.mysql).isSql && (
                      <Button
                        size="small"
                        loading={refreshingDs === ds.id}
                        onClick={() => handleRefreshSchema(ds.id)}
                      >
                        刷新 Schema
                      </Button>
                    )}
                    <Popconfirm
                      title="确认删除数据源?"
                      description="删除后相关知识条目将失效"
                      onConfirm={() => handleDeleteDs(ds.id)}
                    >
                      <Button size="small" danger icon={<DeleteOutlined />}>
                        删除
                      </Button>
                    </Popconfirm>
                  </div>
                </div>
                );
              })}
            </div>
          </div>
        ) : (
          <div className={styles.empty}>暂无命名空间, 请先新建</div>
        )}
      </div>

      <DataSourceFormModal
        open={showDsModal}
        activeNsId={activeNs?.id ?? null}
        onCancel={() => setShowDsModal(false)}
        onSubmitted={() => {
          setShowDsModal(false);
          loadDetail();
        }}
      />
    </div>
  );
};

export default DataSourcePage;
