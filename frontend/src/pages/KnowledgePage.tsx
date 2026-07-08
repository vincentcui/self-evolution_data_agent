/* ════════════════════════════════════════════
 *  知识库管理页 — 顶部概览 + 左侧分组导航 + 右栏列表
 * ════════════════════════════════════════════ */

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Button,
  Form,
  Input,
  Modal,
  Tag,
  message,
} from "antd";
import { PlusOutlined, SyncOutlined } from "@ant-design/icons";
import * as api from "@/api";
import type {
  DataSource,
  GitRepo,
  KnowledgeEntry,
  Namespace,
  TerminologyConflict,
} from "@/types";
import globalStyles from "@/styles/global.module.css";
import styles from "@/styles/knowledge.module.css";
import AuditQueue from "@/components/audit/AuditQueue";
import CreateKnowledgeForm from "@/components/audit/CreateKnowledgeForm";
import TerminologyConflictModal from "@/components/audit/TerminologyConflictModal";
import NamespaceSelector from "@/components/NamespaceSelector";
import { SchemaCanonicalPanel } from "@/components/SchemaCanonicalPanel";
import { ExtractionFailureList } from "@/components/extraction/ExtractionFailureList";

type NavKey =
  | "knowledge"
  | "audit-pending"
  | "audit-rejected"
  | "repos"
  | "terminology-conflict"
  | "schema"
  | "extraction-failure";

const NAV_GROUPS: { title: string; desc: string; items: { key: NavKey; label: string }[] }[] = [
  {
    title: "知识资产",
    desc: "正式知识与 Schema 治理",
    items: [
      { key: "knowledge", label: "知识条目" },
      { key: "schema", label: "Schema 管理" },
    ],
  },
  {
    title: "待处理",
    desc: "需要人工确认的知识治理任务",
    items: [
      { key: "audit-pending", label: "待审 (proposed)" },
      { key: "terminology-conflict", label: "术语冲突" },
    ],
  },
  {
    title: "训练来源",
    desc: "Git 仓库、解析与知识生产入口",
    items: [{ key: "repos", label: "Git 仓库" }],
  },
  {
    title: "历史与异常",
    desc: "拒绝记录和抽取失败排查",
    items: [
      { key: "audit-rejected", label: "历史 (rejected)" },
      { key: "extraction-failure", label: "抽取失败" },
    ],
  },
];

const KnowledgePage: React.FC = () => {
  const [namespaces, setNamespaces] = useState<Namespace[]>([]);
  const [activeNsId, setActiveNsId] = useState<number>();
  const [knowledge, setKnowledge] = useState<KnowledgeEntry[]>([]);
  const [repos, setRepos] = useState<GitRepo[]>([]);
  const [dataSources, setDataSources] = useState<DataSource[]>([]);
  const [showAddKnowledge, setShowAddKnowledge] = useState(false);
  const [showAddRepo, setShowAddRepo] = useState(false);
  const [repoForm] = Form.useForm();
  const [activeTab, setActiveTab] = useState<NavKey>("knowledge");

  /* Terminology Conflict 状态 (Phase 3 Task 3.3) */
  const [terminologyConflicts, setTerminologyConflicts] = useState<TerminologyConflict[]>([]);
  const [selectedTermConflict, setSelectedTermConflict] = useState<TerminologyConflict | null>(null);

  useEffect(() => {
    api.fetchNamespaces().then(setNamespaces);
  }, []);

  const loadData = useCallback(async (nsId: number) => {
    const [k, repoRes, termConflicts, ds] = await Promise.all([
      api.fetchKnowledge(nsId),
      api.fetchRepos(nsId),
      api
        .listTerminologyConflicts(nsId)
        .then((r) => r.conflicts)
        .catch(() => []),
      api.fetchDataSources(nsId).catch(() => []),
    ]);
    setKnowledge(k);
    setRepos(repoRes.repos);
    setTerminologyConflicts(termConflicts);
    setDataSources(ds);
  }, []);

  useEffect(() => {
    if (activeNsId) loadData(activeNsId);
  }, [activeNsId, loadData]);

  const handleAddRepo = async () => {
    if (!activeNsId) return;
    const vals = await repoForm.validateFields();
    await api.addRepo(activeNsId, vals);
    message.success("仓库已添加");
    setShowAddRepo(false);
    repoForm.resetFields();
    loadData(activeNsId);
  };

  const handleParse = async (repoId: number) => {
    if (!activeNsId) return;
    message.loading({ content: "解析中...", key: "parse" });
    try {
      await api.parseRepo(activeNsId, repoId);
      message.success({ content: "解析完成", key: "parse" });
      loadData(activeNsId);
    } catch (e: any) {
      message.error({
        content: e?.response?.data?.detail || "解析失败",
        key: "parse",
      });
    }
  };

  const repoStatusColors: Record<string, string> = {
    pending: "default",
    cloning: "processing",
    parsing: "processing",
    parsed: "success",
    error: "error",
  };

  /* ── 空间准备度指标 ── */
  const readiness = useMemo(() => {
    const dsCount = dataSources.length;
    const parsedRepos = repos.filter((r) => r.parse_status === "parsed").length;
    const totalRepos = repos.length;
    const proposedCount = knowledge.filter((k) => k.status === "proposed").length;
    const canonicalCount = knowledge.filter((k) => k.status === "canonical").length;

    let badge: { text: string; cls: string };
    if (dsCount === 0) {
      badge = { text: "未就绪", cls: styles.readinessBadgeNotReady };
    } else if (parsedRepos < totalRepos || proposedCount > 0 || terminologyConflicts.length > 0) {
      badge = { text: "部分就绪", cls: styles.readinessBadgePartial };
    } else {
      badge = { text: "完全就绪", cls: styles.readinessBadgeReady };
    }
    return { dsCount, parsedRepos, totalRepos, proposedCount, canonicalCount, badge };
  }, [dataSources, repos, knowledge, terminologyConflicts]);

  /* ── 导航徽标 ── */
  const navBadges: Partial<Record<NavKey, number>> = useMemo(() => ({
    "audit-pending": readiness.proposedCount,
    "terminology-conflict": terminologyConflicts.length,
  }), [readiness.proposedCount, terminologyConflicts]);

  const nsName = namespaces.find((n) => n.id === activeNsId)?.name;

  return (
    <div>
      {/* ── 页面头部 ── */}
      <div className={globalStyles.pageHeader}>
        <div>
          <h1 className={globalStyles.pageTitle}>知识库</h1>
          <p className={globalStyles.pageSubtitle}>
            业务术语、查询规则、SQL 示例
          </p>
        </div>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setShowAddKnowledge(true)}
          disabled={!activeNsId}
        >
          添加知识
        </Button>
      </div>

      {!activeNsId ? (
        <div className={styles.empty}>请先选择命名空间</div>
      ) : null}

      {/* ── 空间准备度概览（始终渲染，含命名空间选择器）── */}
      <div className={styles.readinessSection}>
        <div className={styles.readinessHeader}>
          <span className={styles.readinessEyebrow}>空间准备度</span>
          <NamespaceSelector
            style={{ width: 220 }}
            value={activeNsId}
            onChange={(id) => setActiveNsId(id)}
          />
        </div>
        {activeNsId && (
          <>
            <div className={`${styles.readinessBadge} ${readiness.badge.cls}`}>
              {readiness.badge.text}
            </div>
            <div className={styles.readinessCards}>
              <div className={styles.readinessCard}>
                <span className={styles.readinessLabel}>
                  <span className={`${styles.readinessDot} ${styles.dotGreen}`} />
                  数据源
                </span>
                <span className={styles.readinessValue}>{readiness.dsCount}</span>
              </div>
              <div className={styles.readinessCard}>
                <span className={styles.readinessLabel}>
                  <span className={`${styles.readinessDot} ${styles.dotSky}`} />
                  训练来源
                </span>
                <span className={styles.readinessValue}>
                  {readiness.parsedRepos}
                  <span className={styles.readinessValueMuted}> / {readiness.totalRepos}</span>
                </span>
              </div>
              <div className={styles.readinessCard}>
                <span className={styles.readinessLabel}>
                  <span className={`${styles.readinessDot} ${styles.dotYellow}`} />
                  知识审核
                </span>
                <span className={styles.readinessValue}>{readiness.proposedCount}</span>
              </div>
              <div className={styles.readinessCard}>
                <span className={styles.readinessLabel}>
                  <span className={`${styles.readinessDot} ${styles.dotBlue}`} />
                  可问数
                </span>
                <span className={styles.readinessValue}>{readiness.canonicalCount}</span>
              </div>
            </div>
          </>
        )}
      </div>

      {activeNsId && (
        <>
          {/* ── 左右两栏 ── */}
          <div className={styles.layout}>
            {/* 左侧分组导航 */}
            <nav className={styles.sideNav}>
              {NAV_GROUPS.map((group) => (
                <div key={group.title} className={styles.navGroup}>
                  <div className={styles.navGroupTitle}>{group.title}</div>
                  <div className={styles.navGroupDesc}>{group.desc}</div>
                  {group.items.map((item) => {
                    const isActive = activeTab === item.key;
                    const badge = navBadges[item.key];
                    return (
                      <button
                        key={item.key}
                        className={isActive ? styles.navItemActive : styles.navItem}
                        onClick={() => setActiveTab(item.key)}
                      >
                        <span>{item.label}</span>
                        {badge !== undefined && badge > 0 && (
                          <span className={isActive ? styles.navBadgeActive : styles.navBadge}>
                            {badge}
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              ))}
            </nav>

            {/* 右侧内容区 */}
            <div className={styles.contentArea}>
              {activeTab === "knowledge" && (
                <AuditQueue
                  nsId={activeNsId}
                  showStatusFilter
                  onChange={() => activeNsId && loadData(activeNsId)}
                />
              )}

              {activeTab === "audit-pending" && (
                <AuditQueue
                  nsId={activeNsId}
                  status="proposed"
                  onChange={() => activeNsId && loadData(activeNsId)}
                />
              )}

              {activeTab === "audit-rejected" && (
                <AuditQueue
                  nsId={activeNsId}
                  status="rejected"
                  onChange={() => activeNsId && loadData(activeNsId)}
                />
              )}

              {activeTab === "repos" && (
                <>
                  <Button
                    size="small"
                    type="default"
                    icon={<PlusOutlined />}
                    onClick={() => setShowAddRepo(true)}
                    style={{
                      marginBottom: 12,
                      background: "#eff6ff",
                      borderColor: "#dbeafe",
                      color: "#2563eb",
                    }}
                  >
                    添加仓库
                  </Button>
                  {repos.map((repo) => (
                    <div key={repo.id} className={styles.repoCard}>
                      <div className={styles.repoInfo}>
                        <div className={styles.repoUrl}>{repo.url}</div>
                        <div className={styles.repoMeta}>
                          {repo.branch} ·{" "}
                          {repo.parsed_at
                            ? `上次解析: ${repo.parsed_at}`
                            : "未解析"}
                        </div>
                      </div>
                      <div className={styles.repoActions}>
                        <Tag color={repoStatusColors[repo.parse_status]}>
                          {repo.parse_status}
                        </Tag>
                        <Button
                          size="small"
                          icon={<SyncOutlined />}
                          onClick={() => handleParse(repo.id)}
                        >
                          解析
                        </Button>
                      </div>
                    </div>
                  ))}
                </>
              )}

              {/* ── Terminology Conflict tab (Phase 3 Task 3.3) ── */}
              {activeTab === "terminology-conflict" && (
                <>
                  <div style={{ marginBottom: 12, color: "#6b7280", fontSize: 13 }}>
                    术语唯一键冲突: 待处理 {terminologyConflicts.length} 条
                  </div>
                  {terminologyConflicts.length === 0 ? (
                    <div className={styles.empty}>暂无待处理冲突</div>
                  ) : (
                    <div className={styles.list}>
                      {terminologyConflicts.map((c) => {
                        let candTerm = "—";
                        try {
                          candTerm = JSON.parse(c.candidate_payload).term ?? "—";
                        } catch {
                          /* ignore */
                        }
                        return (
                          <div key={c.id} className={styles.card}>
                            <div className={styles.cardHeader}>
                              <Tag color="orange">冲突 #{c.id}</Tag>
                              <Tag>来源: {c.candidate_source}</Tag>
                              <span style={{ color: "#888", fontSize: 12 }}>
                                existing #{c.existing_entry_id} ↔ candidate{" "}
                                <strong>{candTerm}</strong>
                              </span>
                            </div>
                            <div style={{ marginTop: 8 }}>
                              <Button
                                size="small"
                                type="primary"
                                onClick={() => setSelectedTermConflict(c)}
                              >
                                查看 / 解决
                              </Button>
                              <span style={{ marginLeft: 12, color: "#999", fontSize: 12 }}>
                                {c.created_at}
                              </span>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                  {selectedTermConflict && activeNsId && (
                    <TerminologyConflictModal
                      conflict={selectedTermConflict}
                      existing={(() => {
                        const ke = knowledge.find(
                          (k) => k.id === selectedTermConflict.existing_entry_id,
                        );
                        if (!ke) return undefined;
                        try {
                          const p = JSON.parse(ke.content);
                          return { term: p.term ?? "", synonyms: p.synonyms ?? [] };
                        } catch {
                          return undefined;
                        }
                      })()}
                      open
                      onClose={(result) => {
                        setSelectedTermConflict(null);
                        if (result.resolved) loadData(activeNsId);
                      }}
                    />
                  )}
                </>
              )}

              {/* ── Schema 管理 tab ── */}
              {activeTab === "schema" && (
                <>
                  {/* ── 通用 Schema Canonical (MySQL + MongoDB) ── */}
                  {activeNsId && (
                    <SchemaCanonicalPanel namespaceId={activeNsId} />
                  )}
                </>
              )}

              {/* ── 抽取失败 tab ── */}
              {activeTab === "extraction-failure" && activeNsId && (
                <ExtractionFailureList namespaceId={activeNsId} />
              )}
            </div>
          </div>
        </>
      )}

      {/* ── 添加知识弹窗 ── */}
      <CreateKnowledgeForm
        open={showAddKnowledge}
        defaultNamespaceId={activeNsId}
        onClose={() => setShowAddKnowledge(false)}
        onSubmitted={(res) => {
          const overflow = (res as { overflow?: boolean }).overflow;
          const splitCount = (res as { split_candidates?: unknown[] }).split_candidates?.length ?? 0;
          if (overflow) {
            message.warning(
              splitCount > 0
                ? `内容过长, 建议拆分为 ${splitCount} 条分别录入 (条目暂未保存)`
                : "内容过长, 请拆分后分别录入",
            );
            return;
          }
          if (res.conflicts && res.conflicts.length > 0) {
            message.warning(
              `知识已添加, 检测到 ${res.conflicts.length} 条潜在冲突, 详见列表 status 标签`,
            );
          } else {
            message.success("知识已添加");
          }
          setShowAddKnowledge(false);
          if (activeNsId) loadData(activeNsId);
        }}
      />

      {/* ── 添加仓库弹窗 ── */}
      <Modal
        title="添加 Git 仓库"
        open={showAddRepo}
        onOk={handleAddRepo}
        onCancel={() => setShowAddRepo(false)}
      >
        <Form
          form={repoForm}
          layout="vertical"
          initialValues={{ branch: "master" }}
        >
          <Form.Item
            name="url"
            label="仓库地址"
            rules={[{ required: true }]}
          >
            <Input placeholder="https://github.com/org/repo.git" />
          </Form.Item>
          <Form.Item name="branch" label="分支">
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

export default KnowledgePage;
