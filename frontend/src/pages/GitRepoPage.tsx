/* ════════════════════════════════════════════
 *  Git 仓库管理页 — RepoManager (当前空间取自 WorkspacePage 共享 context)
 *
 *  拆分自原 NamespacePage.tsx: Git 仓库 Tab 内容独立成页。
 * ════════════════════════════════════════════ */

import React, { useCallback, useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";
import * as api from "@/api";
import RepoManager from "@/components/RepoManager";
import type { WorkspaceOutletContext } from "@/components/WorkspacePage";
import type { BatchStatus, DataSource, GitRepo } from "@/types";
import styles from "@/styles/namespace.module.css";
import globalStyles from "@/styles/global.module.css";

const GitRepoPage: React.FC = () => {
  const { activeNs } = useOutletContext<WorkspaceOutletContext>();
  const [datasources, setDatasources] = useState<DataSource[]>([]);
  const [repos, setRepos] = useState<GitRepo[]>([]);
  const [batchStatus, setBatchStatus] = useState<BatchStatus | null>(null);

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
              <RepoManager
                nsId={activeNs.id}
                datasources={datasources}
                repos={repos}
                batchStatus={batchStatus}
                onReposChange={reloadRepos}
              />
            </div>
          </div>
        ) : (
          <div className={styles.empty}>暂无命名空间, 请先新建</div>
        )}
      </div>
    </div>
  );
};

export default GitRepoPage;
