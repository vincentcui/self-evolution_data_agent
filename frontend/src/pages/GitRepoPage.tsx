/* ════════════════════════════════════════════
 *  Git 仓库管理页 — RepoManager (当前空间取自 WorkspacePage 共享 context)
 *
 *  拆分自原 NamespacePage.tsx: Git 仓库 Tab 内容独立成页。
 *  Section 8.1: 顶部新增命名空间 Git Token 信息区 (掩码展示 + 编辑 Modal)
 * ════════════════════════════════════════════ */

import React, { useCallback, useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { Alert, Button, Form, Input, Modal, Tag, message } from "antd";
import * as api from "@/api";
import RepoManager from "@/components/RepoManager";
import type { WorkspaceOutletContext } from "@/components/WorkspacePage";
import type { BatchStatus, DataSource, GitRepo } from "@/types";
import styles from "@/styles/namespace.module.css";
import globalStyles from "@/styles/global.module.css";

const GitRepoPage: React.FC = () => {
  const { activeNs, loading, refresh } = useOutletContext<WorkspaceOutletContext>();
  const [datasources, setDatasources] = useState<DataSource[]>([]);
  const [repos, setRepos] = useState<GitRepo[]>([]);
  const [batchStatus, setBatchStatus] = useState<BatchStatus | null>(null);
  // 命名空间 token 编辑 Modal
  const [tokenModalOpen, setTokenModalOpen] = useState(false);
  const [tokenForm] = Form.useForm();
  const [savingToken, setSavingToken] = useState(false);

  const loadDetail = useCallback(async () => {
    if (!activeNs) return;
    const [ds, repoRes] = await Promise.all([
      api.fetchDataSources(activeNs.id),
      api.fetchRepos(activeNs.id),
    ]);
    setDatasources(ds);
    setRepos(repoRes.repos);
    setBatchStatus(repoRes.batch_status);
  }, [activeNs]);

  useEffect(() => {
    loadDetail();
  }, [loadDetail]);

  const reloadRepos = useCallback(async () => {
    if (!activeNs) return;
    const res = await api.fetchRepos(activeNs.id);
    setRepos(res.repos);
    setBatchStatus(res.batch_status);
  }, [activeNs]);

  const handleSaveToken = async () => {
    const vals = await tokenForm.validateFields();
    setSavingToken(true);
    try {
      await api.updateNamespace(activeNs!.id, { git_token: vals.git_token });
      message.success("Token 已更新");
      setTokenModalOpen(false);
      tokenForm.resetFields();
      refresh();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || "更新失败");
    } finally {
      setSavingToken(false);
    }
  };

  return (
    <div>
      <div className={globalStyles.pageHeader}>
        <div>
          <h1 className={globalStyles.pageTitle}>Git 仓库</h1>
          <p className={globalStyles.pageSubtitle}>
            {activeNs ? `当前空间: ${activeNs.name}` : "管理当前空间接入的代码仓库"}
          </p>
        </div>
      </div>

      <div className={styles.container}>
        {activeNs ? (
          <div className={styles.detailPanel}>
            <div className={styles.detailContent}>
              {/* ── 命名空间 Git Token 信息区 (设计 Section 8.1) ── */}
              <Alert
                type="info"
                showIcon
                style={{ marginBottom: 12 }}
                message={
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                    <div>
                      <span style={{ fontWeight: 500 }}>命名空间 Git Token: </span>
                      {activeNs.git_token_masked ? (
                        <Tag color="blue">{activeNs.git_token_masked}</Tag>
                      ) : (
                        <Tag>未配置</Tag>
                      )}
                      <span style={{ fontSize: 12, color: "#94a3b8", marginLeft: 8 }}>
                        未配置时将使用全局配置中心 token 或环境变量兜底
                      </span>
                    </div>
                    <Button
                      size="small"
                      type="link"
                      onClick={() => {
                        tokenForm.resetFields();
                        setTokenModalOpen(true);
                      }}
                    >
                      {activeNs.git_token_masked ? "编辑" : "配置"}
                    </Button>
                  </div>
                }
              />

              <RepoManager
                nsId={activeNs.id}
                datasources={datasources}
                repos={repos}
                batchStatus={batchStatus}
                onReposChange={reloadRepos}
                nsTokenMasked={activeNs.git_token_masked ?? ""}
              />
            </div>
          </div>
        ) : (
          <div className={styles.empty}>{loading ? "加载中..." : "暂无命名空间, 请先新建"}</div>
        )}
      </div>

      {/* ── 命名空间 Token 编辑 Modal ── */}
      <Modal
        title="编辑命名空间 Git Token"
        open={tokenModalOpen}
        onOk={handleSaveToken}
        confirmLoading={savingToken}
        onCancel={() => setTokenModalOpen(false)}
      >
        <Form form={tokenForm} layout="vertical">
          <Form.Item
            name="git_token"
            label="Git Token"
            tooltip="留空清除命名空间级 token, 退回全局配置中心或环境变量兜底"
          >
            <Input.Password placeholder="输入新 token 覆盖 (留空清除)" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

export default GitRepoPage;
