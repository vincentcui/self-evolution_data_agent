/* ════════════════════════════════════════════
 *  RepoManager — Git 仓库管理独立组件
 *  进度条 + 2s 轮询 + 批量操作 + 映射管理 + 报告面板
 * ════════════════════════════════════════════ */

import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Descriptions,
  Form,
  Input,
  Modal,
  Popconfirm,
  Progress,
  Select,
  Space,
  Tag,
  Typography,
  message,
} from "antd";
import {
  ReloadOutlined,
  FileSearchOutlined,
  StopOutlined,
  ThunderboltOutlined,
  DeleteOutlined,
  RobotOutlined,
} from "@ant-design/icons";
import * as api from "@/api";
import type { BatchStatus, DataSource, GitRepo, ParseReport } from "@/types";
import { DB_TYPE_META } from "@/types";
import styles from "@/styles/namespace.module.css";

const { Text } = Typography;

interface Props {
  nsId: number;
  datasources: DataSource[];
  repos: GitRepo[];
  batchStatus?: BatchStatus | null;
  onReposChange: () => void;
}

const RepoManager: React.FC<Props> = ({ nsId, datasources, repos, batchStatus, onReposChange }) => {
  const [repoForm] = Form.useForm();
  const [parsing, setParsing] = useState<Set<number>>(new Set());
  const [profiles, setProfiles] = useState<api.ProfileOut[]>([]);

  useEffect(() => {
    api.fetchProfiles().then(setProfiles).catch(() => {});
  }, []);

  const profileOptions = profiles
    .filter((p) => p.is_enabled)
    .map((p) => ({ label: `${p.display_name}${p.description ? ` — ${p.description}` : ""}`, value: p.id }));

  /* ── 报告展开 ── */
  const [expandedRepoId, setExpandedRepoId] = useState<number | null>(null);
  const [report, setReport] = useState<ParseReport | null>(null);

  /* ── 映射面板 ── */
  const [mappingRepoId, setMappingRepoId] = useState<number | null>(null);
  const [mappings, setMappings] = useState<any[]>([]);

  /* ── 2s 轮询: 有活跃 worker 时刷新 ── */
  const pollRef = useRef<ReturnType<typeof setInterval>>();

  const hasActiveWorker = repos.some((r) => r.worker_id) || !!batchStatus?.active;

  useEffect(() => {
    if (hasActiveWorker) {
      pollRef.current = setInterval(onReposChange, 2000);
      return () => {
        if (pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = undefined;
        }
      };
    }
  }, [hasActiveWorker, onReposChange]);

  /* ── CRUD ── */
  const handleAddRepo = async () => {
    const vals = await repoForm.validateFields();
    await api.addRepo(nsId, vals);
    message.success("仓库已添加");
    repoForm.resetFields();
    onReposChange();
  };

  const handleDeleteRepo = async (repoId: number) => {
    await api.deleteRepo(nsId, repoId);
    message.success("仓库已删除");
    onReposChange();
  };

  const handleChangeProfile = async (repoId: number, profileId: number | null) => {
    try {
      await api.updateRepoProfile(nsId, repoId, profileId);
      message.success("Profile 已更新，重新解析以应用");
      onReposChange();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || "更新失败");
    }
  };

  const handleParse = async (repoId: number) => {
    setParsing((prev) => new Set(prev).add(repoId));
    try {
      await api.parseRepo(nsId, repoId);
      message.success("解析已启动");
      onReposChange();
    } catch (e: any) {
      message.error(e?.response?.data?.detail || "启动失败");
    } finally {
      setParsing((prev) => { const s = new Set(prev); s.delete(repoId); return s; });
    }
  };

  const handleCancel = async (repoId: number) => {
    try {
      const res = await api.cancelParse(nsId, repoId);
      if (res.cancelled) {
        message.success("已取消");
      } else {
        message.warning("任务已结束，无需取消");
      }
      onReposChange();
    } catch {
      message.error("取消失败");
    }
  };

  const handleBatchParse = async (force = false) => {
    try {
      const res = await api.batchParseRepos(nsId, force);
      // force 清场是两阶段异步, 后端 message 如实反映「清场中…」; 增量则为「已启动 N 个」
      message.success(res.message || `已启动 ${res.started} 个解析任务`);
      onReposChange();
    } catch {
      message.error("批量解析失败");
    }
  };

  const handleFullRebuild = async () => {
    Modal.confirm({
      title: "全量解析将清除本命名空间历史知识",
      content: (
        <div>
          <p>本次操作将清除所有仓库历史抽取的知识 (含已审核 canonical)，重新生成后需要人工审核。</p>
        </div>
      ),
      okText: "确认清场并重新解析",
      okType: "danger",
      cancelText: "取消",
      onOk: () => handleBatchParse(true),
    });
  };

  const handleViewReport = async (repoId: number) => {
    try {
      const r = await api.getParseReport(nsId, repoId);
      setReport(r);
      setExpandedRepoId(repoId);
    } catch {
      message.error("暂无报告");
    }
  };

  /* ── 映射管理 ── */
  const loadMappings = useCallback(async (repoId: number) => {
    const m = await api.fetchRepoMappings(nsId, repoId);
    setMappings(m);
  }, [nsId]);

  const toggleMapping = async (repoId: number) => {
    if (mappingRepoId === repoId) {
      setMappingRepoId(null);
      return;
    }
    setMappingRepoId(repoId);
    await loadMappings(repoId);
  };

  const handleAddMapping = async (dsId: number) => {
    if (!mappingRepoId) return;
    await api.addRepoMapping(nsId, mappingRepoId, dsId);
    await loadMappings(mappingRepoId);
  };

  const handleDeleteMapping = async (mappingId: number) => {
    if (!mappingRepoId) return;
    await api.deleteRepoMapping(nsId, mappingRepoId, mappingId);
    await loadMappings(mappingRepoId);
  };

  const scoreColor = (score: number) => {
    if (score >= 90) return "#10b981";
    if (score >= 70) return "#2563eb";
    if (score >= 50) return "#f59e0b";
    return "#ef4444";
  };

  const pendingCount = repos.filter((r) => r.parse_status === "pending" || r.parse_status === "error").length;
  const parsableCount = repos.filter((r) => !r.worker_id).length;

  return (
    <div className={styles.detailContent}>
      {/* ── 顶部操作栏 ── */}
      <div style={{ display: "flex", gap: 8, marginBottom: 12, alignItems: "center" }}>
        <Form form={repoForm} layout="inline" style={{ flex: 1 }}>
          <Form.Item name="url" rules={[{ required: true, message: "请输入 URL" }]}>
            <Input placeholder="https://github.com/org/repo.git" style={{ width: 280 }} />
          </Form.Item>
          <Form.Item name="branch" initialValue="master">
            <Input placeholder="分支" style={{ width: 100 }} />
          </Form.Item>
          <Form.Item name="profile_id" label="Profile" tooltip="选择正确的 profile 可提高 schema 识别准确率。不确定可不选。">
            <Select allowClear placeholder="不选 (自动识别)" style={{ width: 220 }} options={profileOptions} />
          </Form.Item>
          <Button type="primary" onClick={handleAddRepo}>添加</Button>
        </Form>
        {pendingCount > 0 && (
          <Button icon={<ThunderboltOutlined />} onClick={() => handleBatchParse(false)}>
            批量解析 ({pendingCount})
          </Button>
        )}
        {parsableCount > 0 && (
          <Button icon={<ReloadOutlined />} onClick={handleFullRebuild}>
            全量解析 ({parsableCount})
          </Button>
        )}
      </div>

      {/* ── 二轮自答进度 ── */}
      {batchStatus?.active && (
        <Alert
          type="info"
          showIcon
          icon={<RobotOutlined spin />}
          message={`跨仓库知识自答中 ${batchStatus.progress ? `(${batchStatus.progress})` : ""}`}
          description={batchStatus.message}
          style={{ marginBottom: 12 }}
        />
      )}

      {/* ── 仓库列表 ── */}
      {repos.map((repo) => (
        <div key={repo.id}>
          <div className={styles.repoCard}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className={styles.dsName} style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {repo.url}
              </div>
              <div className={styles.dsMeta}>
                {repo.branch} · {repo.parsed_at ? `上次解析: ${repo.parsed_at}` : "未解析"}
              </div>
              <div style={{ marginTop: 4 }}>
                <Select
                  size="small"
                  allowClear
                  placeholder="Profile: 不选 (自动识别)"
                  style={{ width: 260 }}
                  value={repo.profile_id ?? undefined}
                  options={profileOptions}
                  onChange={(v) => handleChangeProfile(repo.id, (v as number) ?? null)}
                />
              </div>
              {/* 进度条 */}
              {repo.worker_id && (
                <div style={{ marginTop: 6 }}>
                  <Progress percent={repo.progress} size="small" status="active" />
                  <div style={{ fontSize: 11, color: "#94a3b8" }}>{repo.progress_message}</div>
                </div>
              )}
            </div>
            <div className={styles.dsActions}>
              <Tag color={
                repo.parse_status === "parsed" ? "success"
                  : repo.parse_status === "error" ? "error"
                    : repo.parse_status === "cloning" || repo.parse_status === "parsing" ? "processing"
                      : "default"
              }>
                {repo.parse_status}
              </Tag>
              {repo.has_report && (
                <Tag color={scoreColor(repo.completeness_score)}>{repo.completeness_score}分</Tag>
              )}
              {repo.worker_id ? (
                <Button size="small" danger icon={<StopOutlined />} onClick={() => handleCancel(repo.id)}>
                  取消
                </Button>
              ) : (
                <Button size="small" type="primary" icon={<ReloadOutlined />}
                  loading={parsing.has(repo.id)} onClick={() => handleParse(repo.id)}>
                  解析
                </Button>
              )}
              {repo.has_report && (
                <Button size="small" icon={<FileSearchOutlined />}
                  onClick={() => expandedRepoId === repo.id ? setExpandedRepoId(null) : handleViewReport(repo.id)}>
                  {expandedRepoId === repo.id ? "收起" : "报告"}
                </Button>
              )}
              <Button size="small" onClick={() => toggleMapping(repo.id)}>
                {mappingRepoId === repo.id ? "收起映射" : "映射"}
              </Button>
              <Popconfirm
                title="确认删除仓库?"
                description="删除后相关知识条目将失效"
                onConfirm={() => handleDeleteRepo(repo.id)}
              >
                <Button size="small" danger icon={<DeleteOutlined />}>
                  删除
                </Button>
              </Popconfirm>
            </div>
          </div>

          {/* ── 映射面板 ── */}
          {mappingRepoId === repo.id && (
            <div style={{ padding: "8px 12px", background: "#f8fafc", borderRadius: 8, marginBottom: 8, border: "1px solid #e0e7ff" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                <div style={{ fontSize: 12, fontWeight: 500, color: "#475569" }}>数据源映射</div>
                <div style={{ fontSize: 11, color: "#94a3b8", fontStyle: "italic" }}>
                  💡 可同时映射多个数据源，训练时按类型自动分流
                </div>
              </div>
              {mappings.length === 0 && (
                <div style={{ fontSize: 11, color: "#f59e0b", background: "#fffbeb", padding: "6px 8px", borderRadius: 4, marginBottom: 6 }}>
                  ⚠️ 未映射数据源，解析结果将训练到命名空间的所有数据源
                </div>
              )}
              {/* 智能推荐：混合数据库提示 */}
              {report && report.ddls_trained > 0 && report.docs_trained > 0 && mappings.length < 2 && (
                <div style={{ fontSize: 11, color: "#0f766e", background: "#f0fdfa", padding: "6px 8px", borderRadius: 4, marginBottom: 6, border: "1px solid #99f6e4" }}>
                  💡 此仓库包含混合数据库架构（MySQL {report.ddls_trained} 个表 + MongoDB 集合），建议同时映射两种类型的数据源
                </div>
              )}
              {mappings.map((m: any) => {
                const ds = datasources.find((d: any) => d.id === m.datasource_id);
                const dbType = ds?.db_type as keyof typeof DB_TYPE_META | undefined;
                return (
                <div key={m.id} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                  <Tag color={DB_TYPE_META[dbType!]?.color ?? "green"}>
                    {ds?.database || `DS#${m.datasource_id}`}
                    ({ds?.db_type})
                  </Tag>
                  <Popconfirm title="移除映射?" onConfirm={() => handleDeleteMapping(m.id)}>
                    <Button size="small" danger type="link">移除</Button>
                  </Popconfirm>
                </div>
                );
              })}
              <Select
                placeholder="添加数据源映射"
                style={{ width: 300, marginTop: 4 }}
                onChange={(v) => handleAddMapping(v)}
                value={null as any}
                options={datasources
                  .filter((d) => !mappings.some((m: any) => m.datasource_id === d.id))
                  .map((d) => ({ value: d.id, label: `${d.database} (${d.db_type})` }))}
              />
            </div>
          )}

          {/* ── 内嵌报告面板 ── */}
          {expandedRepoId === repo.id && report && (
            <div className={styles.reportPanel}>
              <div className={styles.reportScore}>
                <Progress type="circle" size={80} percent={report.completeness_score}
                  strokeColor={scoreColor(report.completeness_score)} format={(p) => `${p}分`} />
                <div style={{ marginTop: 4, color: "#64748b", fontSize: 12 }}>耗时 {report.duration_seconds}s</div>
              </div>
              <Descriptions size="small" bordered column={2} style={{ marginBottom: 12 }}>
                <Descriptions.Item label="扫描">{report.stats.files_scanned}</Descriptions.Item>
                <Descriptions.Item label="成功">{report.stats.files_parsed}</Descriptions.Item>
                <Descriptions.Item label="跳过">{report.stats.files_skipped}</Descriptions.Item>
                <Descriptions.Item label="失败">{report.stats.files_errored}</Descriptions.Item>
              </Descriptions>
              {report.evaluation_summary && (
                <div style={{ background: "white", borderRadius: 8, padding: 12, marginBottom: 12, border: "1px solid #e0e7ff", fontSize: 13, color: "#1e3a5f" }}>
                  {report.evaluation_summary}
                </div>
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  );
};

export default RepoManager;
