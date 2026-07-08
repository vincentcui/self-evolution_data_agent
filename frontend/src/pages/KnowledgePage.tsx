/* ════════════════════════════════════════════
 *  知识库管理页 — SegmentedControl 筛选 + 卡片列表
 * ════════════════════════════════════════════ */

import React, { useCallback, useEffect, useState } from "react";
import {
  Button,
  Form,
  Input,
  Modal,
  Tag,
  message,
} from "antd";
import { PlusOutlined, SyncOutlined } from "@ant-design/icons";
import { useOutletContext } from "react-router-dom";
import * as api from "@/api";
import type {
  GitRepo,
  KnowledgeEntry,
  TerminologyConflict,
} from "@/types";
import globalStyles from "@/styles/global.module.css";
import styles from "@/styles/knowledge.module.css";
import AuditQueue from "@/components/audit/AuditQueue";
import CreateKnowledgeForm from "@/components/audit/CreateKnowledgeForm";
import TerminologyConflictModal from "@/components/audit/TerminologyConflictModal";
import type { WorkspaceOutletContext } from "@/components/WorkspacePage";
import { SchemaCanonicalPanel } from "@/components/SchemaCanonicalPanel";
import { ExtractionFailureList } from "@/components/extraction/ExtractionFailureList";

const KnowledgePage: React.FC = () => {
  const { activeNs } = useOutletContext<WorkspaceOutletContext>();
  const activeNsId = activeNs?.id;
  const [knowledge, setKnowledge] = useState<KnowledgeEntry[]>([]);
  const [repos, setRepos] = useState<GitRepo[]>([]);
  const [showAddKnowledge, setShowAddKnowledge] = useState(false);
  const [showAddRepo, setShowAddRepo] = useState(false);
  const [repoForm] = Form.useForm();
  const [activeTab, setActiveTab] = useState<
    | "knowledge"
    | "audit-pending"
    | "audit-rejected"
    | "repos"
    | "terminology-conflict"
    | "schema"
    | "extraction-failure"
  >("knowledge");

  /* Terminology Conflict 状态 (Phase 3 Task 3.3) */
  const [terminologyConflicts, setTerminologyConflicts] = useState<TerminologyConflict[]>([]);
  const [selectedTermConflict, setSelectedTermConflict] = useState<TerminologyConflict | null>(null);

  const loadData = useCallback(async (nsId: number) => {
    const [k, repoRes, termConflicts] = await Promise.all([
      api.fetchKnowledge(nsId),
      api.fetchRepos(nsId),
      api
        .listTerminologyConflicts(nsId)
        .then((r) => r.conflicts)
        .catch(() => []),
    ]);
    setKnowledge(k);
    setRepos(repoRes.repos);
    setTerminologyConflicts(termConflicts);
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

  /* ── SegmentedControl 渲染器 ── */
  const Seg: React.FC<{
    options: { value: string; label: string }[];
    value: string;
    onChange: (v: string) => void;
  }> = ({ options, value, onChange }) => (
    <div className={styles.filterGroup}>
      {options.map((o) => (
        <button
          key={o.value}
          className={
            value === o.value ? styles.filterItemActive : styles.filterItem
          }
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );

  const repoStatusColors: Record<string, string> = {
    pending: "default",
    cloning: "processing",
    parsing: "processing",
    parsed: "success",
    error: "error",
  };

  return (
    <div>
      {/* ── 页面头部 ── */}
      <div className={globalStyles.pageHeader}>
        <div style={{ display: "flex", alignItems: "center" }}>
          <div>
            <h1 className={globalStyles.pageTitle}>知识库</h1>
            <p className={globalStyles.pageSubtitle}>
              {activeNs ? `当前空间: ${activeNs.name}` : "业务术语、查询规则、SQL 示例"}
            </p>
          </div>
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
      ) : (
        <>
          {/* ── Tab 栏 ── */}
          <div className={styles.tabBar}>
            <button
              className={
                activeTab === "knowledge"
                  ? styles.tabItemActive
                  : styles.tabItem
              }
              onClick={() => setActiveTab("knowledge")}
            >
              知识条目
            </button>
            <button
              className={
                activeTab === "audit-pending" ? styles.tabItemActive : styles.tabItem
              }
              onClick={() => setActiveTab("audit-pending")}
            >
              待审 (proposed)
            </button>
            <button
              className={
                activeTab === "audit-rejected" ? styles.tabItemActive : styles.tabItem
              }
              onClick={() => setActiveTab("audit-rejected")}
            >
              历史 (rejected)
            </button>
            <button
              className={
                activeTab === "repos" ? styles.tabItemActive : styles.tabItem
              }
              onClick={() => setActiveTab("repos")}
            >
              Git 仓库
            </button>
            <button
              className={
                activeTab === "terminology-conflict" ? styles.tabItemActive : styles.tabItem
              }
              onClick={() => setActiveTab("terminology-conflict")}
            >
              术语冲突{terminologyConflicts.length > 0 && ` (${terminologyConflicts.length})`}
            </button>
            <button
              className={
                activeTab === "schema" ? styles.tabItemActive : styles.tabItem
              }
              onClick={() => setActiveTab("schema")}
            >
              Schema 管理
            </button>
            <button
              className={
                activeTab === "extraction-failure" ? styles.tabItemActive : styles.tabItem
              }
              onClick={() => setActiveTab("extraction-failure")}
            >
              抽取失败
            </button>
          </div>

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
