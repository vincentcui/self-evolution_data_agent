/* ════════════════════════════════════════════
 *  工作台首页 — 我的空间 + 统计卡 + 最近使用 + 科学使用路径
 * ----------------------------------------------------------------------------
 *  独立全屏着陆页 (脱离 WorkspacePage 侧边栏布局)。点击"进入空间"记住选中
 *  的命名空间 (localStorage) 后跳转到 /workspace/manage/datasources, 管理页
 *  经 WorkspacePage 共享 context (useActiveNamespace) 读取该记忆并回填。
 * ════════════════════════════════════════════ */

import React, { useCallback, useEffect, useState } from "react";
import { Button, Dropdown, Form, Input, Modal, Popconfirm, Tag, message } from "antd";
import { DeleteOutlined, HomeOutlined, MoreOutlined, PlusOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import type { MenuProps } from "antd";
import * as api from "@/api";
import { useAuth } from "@/context/AuthContext";
import { clearLastNamespaceId, writeLastNamespaceId, readLastNamespaceId } from "@/hooks/useLastNamespaceId";
import type { WorkbenchSummary } from "@/types";
import styles from "@/styles/workbench.module.css";

/* ── 科学使用路径 — 静态引导, 与图1一致 ── */
const USAGE_PATH = [
  { step: "1 创建空间", desc: "独立多库隔离数据、知识和权限，形成清晰的上下文边界。" },
  { step: "2 添加数据源", desc: "接入 MySQL 或 MongoDB，完成空间可访问的基础数据接入。" },
  { step: "3 配置 API Key", desc: "填入大模型 API Key，配置当前空间可用的模型能力。" },
  { step: "4 采集 Schema & 接入 Git", desc: "采集表结构，字段和字段业务含义，接入代码仓库理解上下文语义。" },
  { step: "5 补充知识", desc: "补充术语、字段业务含义与业务规则，减少歧义。" },
  { step: "6 持续沉淀", desc: "从对话与 Trace 中沉淀经验知识，让空间越用越懂。" },
];

const WorkbenchHomePage: React.FC = () => {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [summary, setSummary] = useState<WorkbenchSummary | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [deletingNsId, setDeletingNsId] = useState<number | null>(null);
  const [form] = Form.useForm();

  const loadSummary = useCallback(async () => {
    const data = await api.getWorkbenchSummary();
    setSummary(data);
  }, []);

  useEffect(() => {
    loadSummary();
  }, [loadSummary]);

  const handleEnterNamespace = (nsId: number) => {
    writeLastNamespaceId(nsId);
    navigate("/workspace/manage/datasources");
  };

  const handleCreate = async () => {
    const vals = await form.validateFields();
    await api.createNamespace(vals);
    message.success("创建成功");
    setShowCreate(false);
    form.resetFields();
    loadSummary();
  };

  const handleRecentSessionClick = (namespaceId: number) => {
    writeLastNamespaceId(namespaceId);
    navigate("/");
  };

  const handleDeleteNamespace = async (nsId: number) => {
    await api.deleteNamespace(nsId);
    message.success("已删除");
    if (readLastNamespaceId() === nsId) {
      clearLastNamespaceId();
    }
    loadSummary();
  };

  /** 待配置空间的第一条缺失项提示 */
  const getPendingHint = (ns: NonNullable<WorkbenchSummary["namespaces"]>[number]): string | null => {
    if (ns.ready) return null;
    if (ns.datasource_count === 0) return "添加数据源后即可开始问数";
    if (ns.git_parsed_count === 0 && ns.git_total_count > 0) return "Git 仓库尚未完成解析";
    if (ns.knowledge_count === 0) return "建议采集 Schema 或补充知识";
    return "配置未完成，进入空间继续配置";
  };

  return (
    <div className={styles.page}>
      {/* ── 头部 ── */}
      <div className={styles.header}>
        <div>
          <h1 className={styles.title}>工作台</h1>
          <p className={styles.greeting}>你好，{user?.username}~</p>
          <p className={styles.subtitle}>
            管理你的数据空间，快速进入知识、可以继续未完成的配置。
          </p>
        </div>
        <div className={styles.headerActions}>
          <Button icon={<HomeOutlined />} onClick={() => navigate("/")}>
            首页
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setShowCreate(true)}>
            创建空间
          </Button>
        </div>
      </div>

      {/* ── 统计卡片 ── */}
      <div className={styles.statsRow}>
        <div className={styles.statCard}>
          <div className={styles.statLabel}>可访问空间</div>
          <div className={styles.statValue}>{summary?.accessible_count ?? 0}</div>
        </div>
        <div className={styles.statCard}>
          <div className={styles.statLabel}>可用数空间</div>
          <div className={styles.statValue}>{summary?.ready_count ?? 0}</div>
        </div>
        <div className={styles.statCard}>
          <div className={styles.statLabel}>待配置空间</div>
          <div className={styles.statValue}>{summary?.pending_count ?? 0}</div>
        </div>
        <div className={styles.statCard}>
          <div className={styles.statLabel}>最近会话</div>
          <div className={styles.statValue}>{summary?.recent_session_count ?? 0}</div>
        </div>
      </div>

      {/* ── 我的空间 + 最近使用 ── */}
      <div className={styles.mainGrid}>
        <div className={styles.sectionCardFixed}>
          <h2 className={styles.sectionTitle}>我的空间</h2>
          <p className={styles.sectionSubtitle}>
            进入具体空间后管理数据源、Schema、知识、Git 相关内容。
          </p>
          {summary && summary.namespaces.length === 0 ? (
            <div className={styles.nsEmpty}>暂无可访问空间，请先创建</div>
          ) : (
            <div className={styles.nsGrid}>
              {summary?.namespaces.map((ns) => {
                const pendingHint = getPendingHint(ns);
                const menuItems: MenuProps["items"] = [
                  {
                    key: "delete",
                    label: (
                      <Popconfirm
                        title="确认删除该空间?"
                        open={deletingNsId === ns.id}
                        onConfirm={async () => { await handleDeleteNamespace(ns.id); setDeletingNsId(null); }}
                        onCancel={() => setDeletingNsId(null)}
                      >
                        <span
                          style={{ color: "#ff4d4f", display: "flex", alignItems: "center", gap: 6 }}
                          onClick={(e) => { e.stopPropagation(); setDeletingNsId(ns.id); }}
                        >
                          <DeleteOutlined /> 删除
                        </span>
                      </Popconfirm>
                    ),
                  },
                ];
                return (
                  <div className={styles.nsCard} key={ns.id}>
                    {/* 右上角状态标签 (绝对定位) */}
                    <Tag
                      color={ns.ready ? "success" : "warning"}
                      className={styles.nsStatusTag}
                      style={{ fontSize: 11, lineHeight: "18px", padding: "0 5px" }}
                    >
                      {ns.ready ? "可问数" : "待配置"}
                    </Tag>
                    {/* 空间名 */}
                    <div className={styles.nsName}>{ns.name}</div>
                    {/* 统计信息 */}
                    <div className={styles.nsMeta}>
                      数据源 {ns.datasource_count} 个 · 会话 {ns.session_count} 个
                      <br />
                      Git {ns.git_parsed_count}/{ns.git_total_count} 已解析 · 知识 {ns.knowledge_count} 条
                    </div>
                    {/* 待配置提示 */}
                    {pendingHint && (
                      <div className={styles.nsPendingHint}>{pendingHint}</div>
                    )}
                    {/* 底部操作行: 进入空间 + ... */}
                    <div style={{ display: "flex", gap: 6, marginTop: "auto" }}>
                      <Button
                        type="primary"
                        size="small"
                        style={{ flex: 1 }}
                        onClick={() => handleEnterNamespace(ns.id)}
                      >
                        进入空间
                      </Button>
                      <Dropdown menu={{ items: menuItems }} trigger={["click"]} placement="bottomRight">
                        <Button
                          className={styles.nsMoreBtn}
                          type="default"
                          size="small"
                          onClick={(e) => e.stopPropagation()}
                          style={{ padding: "0 8px" }}
                        >
                          <MoreOutlined />
                        </Button>
                      </Dropdown>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className={styles.sectionCardFixed}>
          <h2 className={styles.sectionTitle}>最近使用</h2>
          <p className={styles.sectionSubtitle}>最近会话，帮助快速回到工作台。</p>
          {summary && summary.recent_sessions.length === 0 ? (
            <div className={styles.recentEmpty}>暂无最近会话</div>
          ) : (
            <div className={styles.recentList}>
              {summary?.recent_sessions.map((s) => (
                <div
                  className={styles.recentItem}
                  key={s.id}
                  onClick={() => handleRecentSessionClick(s.namespace_id)}
                >
                  <div className={styles.recentTitle}>{s.title}</div>
                  <div className={styles.recentMeta}>{s.namespace_name}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ── 科学使用路径 ── */}
      <div className={styles.sectionCard}>
        <h2 className={styles.sectionTitle}>科学使用路径</h2>
        <p className={styles.sectionSubtitle}>
          这些步骤是相互独立的门槛，进入具体空间后可继续完善。
        </p>
        <div className={styles.pathGrid}>
          {USAGE_PATH.map((p) => (
            <div className={styles.pathCard} key={p.step}>
              <div className={styles.pathStep}>{p.step}</div>
              <div className={styles.pathDesc}>{p.desc}</div>
            </div>
          ))}
        </div>
      </div>

      {/* ── 创建空间 Modal (与 NamespacePage 一致的字段) ── */}
      <Modal
        title="创建命名空间"
        open={showCreate}
        onOk={handleCreate}
        onCancel={() => setShowCreate(false)}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item
            name="slug"
            label="标识 (英文)"
            rules={[{ required: true, pattern: /^[a-z0-9_-]+$/ }]}
          >
            <Input placeholder="如: my-namespace" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

export default WorkbenchHomePage;
