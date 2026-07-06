/* ════════════════════════════════════════════
 *  模型配置管理页 — 参考 DataAgent 设计实现
 * ════════════════════════════════════════════ */
import React, { useEffect, useRef, useState } from "react";
import { Button, message, Select } from "antd";
import { PlusOutlined, ReloadOutlined } from "@ant-design/icons";
import {
  type ModelConfig,
  activateModelConfig,
  deleteModelConfig,
  listModelConfigs,
  testModelConnection,
} from "@/api/modelConfig";
import ModelForm from "./ModelForm";
import { hasOtherActiveEmbedding, isEmbeddingEditLocked } from "./modelFormUtils";
import styles from "./ModelManagement.module.css";

/* ── 厂商元信息 ──────────────────────────────────────────── */
const PROVIDER_META: Record<string, { label: string; abbr: string; cls: string }> = {
  deepseek:    { label: "DeepSeek",               abbr: "DS", cls: styles.provDeepseek },
  qwen:        { label: "Qwen",                   abbr: "Qw", cls: styles.provQwen },
  openai:      { label: "OpenAI",                 abbr: "AI", cls: styles.provOpenai },
  siliconflow: { label: "Siliconflow",            abbr: "Si", cls: styles.provSilicon },
  zhipu:       { label: "智谱 AI",               abbr: "ZP", cls: styles.provZhipu },
  anthropic:   { label: "Anthropic",              abbr: "An", cls: styles.provAnthropic },
  custom:      { label: "Custom",                 abbr: "Cu", cls: styles.provCustom },
};

export default function ModelManagement() {
  const [configs, setConfigs] = useState<ModelConfig[]>([]);
  const [loading, setLoading] = useState(false);
  const [typeFilter, setTypeFilter] = useState<string>("");
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<ModelConfig | null>(null);
  const [testingId, setTestingId] = useState<number | null>(null);
  const [activatingId, setActivatingId] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const load = async () => {
    setLoading(true);
    try { setConfigs(await listModelConfigs()); }
    catch { message.error("加载失败"); }
    finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  const filtered = typeFilter
    ? configs.filter((c) => c.model_type === typeFilter)
    : configs;

  const handleTest = async (cfg: ModelConfig) => {
    setTestingId(cfg.id!);
    try {
      const res = await testModelConnection(cfg);
      if (res.success) message.success(`"${cfg.model_name}" 连接测试成功`);
      else message.warning(res.message);
    } catch { message.error("测试请求失败"); }
    finally { setTestingId(null); }
  };

  const handleActivate = async (cfg: ModelConfig) => {
    if (cfg.model_type === "EMBEDDING" && hasOtherActiveEmbedding(configs, cfg.id!)) {
      message.error("Embedding 模型切换需要重建知识库索引，首期不支持直接热切换");
      return;
    }
    setActivatingId(cfg.id!);
    try {
      await activateModelConfig(cfg.id!);
      message.success(cfg.model_type === "CHAT" ? "已激活，Chat 模型热切换成功" : "Embedding 配置已激活");
      load();
    } catch (e: any) { message.error(e?.response?.data?.detail ?? "激活失败"); }
    finally { setActivatingId(null); }
  };

  const handleDelete = async (cfg: ModelConfig) => {
    if (deletingId === cfg.id) {
      try {
        await deleteModelConfig(cfg.id!);
        message.success("已删除");
        setDeletingId(null);
        load();
      } catch (e: any) { message.error(e?.response?.data?.detail ?? "删除失败"); }
    } else {
      setDeletingId(cfg.id!);
      setTimeout(() => setDeletingId(null), 3000);
    }
  };

  const getProviderMeta = (p: string) => PROVIDER_META[p.toLowerCase()] ?? PROVIDER_META.custom;

  return (
    <div className={styles.page}>
      {/* 页头 */}
      <div className={styles.viewHead}>
        <div>
          <h2 className={styles.title}>模型配置管理</h2>
          <p className={styles.desc}>配置和管理 AI 模型参数，Chat 支持运行时切换；Embedding 切换需重建知识库索引</p>
        </div>
      </div>

      {/* 工具栏 */}
      <div className={styles.toolbar}>
        <div className={styles.toolbarLeft}>
          <Button
            type="primary"
            icon={<PlusOutlined />}
            className={styles.btnPrimary}
            onClick={() => { setEditing(null); setFormOpen(true); }}
          >
            新增配置
          </Button>
          <Button
            icon={<ReloadOutlined />}
            className={styles.btnOutline}
            loading={loading}
            onClick={load}
          >
            刷新
          </Button>
        </div>
        <div className={styles.toolbarRight}>
          <Select
            value={typeFilter || undefined}
            placeholder="按模型类型筛选"
            allowClear
            onChange={(v) => setTypeFilter(v ?? "")}
            className={styles.filterSelect}
            options={[
              { value: "CHAT",      label: "对话模型" },
              { value: "EMBEDDING", label: "嵌入模型" },
            ]}
          />
        </div>
      </div>

      {/* 表格 */}
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <colgroup>
            <col style={{ width: "4%" }} />
            <col style={{ width: "8%" }} />
            <col style={{ width: "11%" }} />
            <col style={{ width: "7%" }} />
            <col style={{ width: "7%" }} />
            <col style={{ width: "14%" }} />
            <col style={{ width: "11%" }} />
            <col style={{ width: "4%" }} />
            <col style={{ width: "6%" }} />
            <col style={{ width: "6%" }} />
            <col style={{ width: "22%" }} />
          </colgroup>
          <thead>
            <tr>
              <th>ID</th><th>提供商</th><th>模型名称</th><th>模型类型</th>
              <th>协议</th><th>API 地址</th><th>路径配置</th><th>温度</th><th>最大 Token</th>
              <th>状态</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={11} className={styles.emptyCell}>
                  暂无模型配置数据
                </td>
              </tr>
            ) : filtered.map((cfg) => {
              const pm = getProviderMeta(cfg.provider);
              const effectiveProtocol = cfg.protocol ?? (cfg.provider === "anthropic" ? "anthropic" : "openai");
              const pathDisplay = cfg.model_type === "CHAT"
                ? (cfg.completions_path ? `对话: ${cfg.completions_path}` : "使用默认路径")
                : (cfg.embeddings_path  ? `嵌入: ${cfg.embeddings_path}`  : "使用默认路径");
              return (
                <tr key={cfg.id} className={cfg.is_active ? styles.activeRow : ""}>
                  <td>{cfg.id}</td>
                  <td>
                    <span className={`${styles.badgeProvider} ${pm.cls}`}>
                      {pm.label}
                    </span>
                  </td>
                  <td><strong>{cfg.model_name}</strong></td>
                  <td>
                    <span className={`${styles.badgeType} ${cfg.model_type === "CHAT" ? styles.typeChat : styles.typeEmbed}`}>
                      {cfg.model_type === "CHAT" ? "对话模型" : "嵌入模型"}
                    </span>
                  </td>
                  <td>
                    <span className={`${styles.badgeProtocol} ${effectiveProtocol === "anthropic" ? styles.protocolAnthropic : styles.protocolOpenai}`}>
                      {effectiveProtocol === "anthropic" ? "Anthropic" : "OpenAI"}
                    </span>
                  </td>
                  <td className={styles.tdUrl} title={cfg.base_url}>
                    <span className={styles.mono}>{cfg.base_url}</span>
                  </td>
                  <td>
                    <span className={styles.pathTag}>{pathDisplay}</span>
                  </td>
                  <td>{cfg.model_type === "CHAT" ? (cfg.temperature ?? 0) : "—"}</td>
                  <td>{cfg.model_type === "CHAT" ? (cfg.max_tokens ?? 12288) : "—"}</td>
                  <td>
                    <span className={cfg.is_active ? styles.badgeActive : styles.badgeDisabled}>
                      {cfg.is_active ? "已激活" : "未激活"}
                    </span>
                  </td>
                  <td>
                    <div className={styles.rowActions}>
                      {/* 连接测试 */}
                      <button
                        className={`${styles.actBtn} ${styles.actTest} ${testingId === cfg.id ? styles.testing : ""}`}
                        onClick={() => handleTest(cfg)}
                        disabled={testingId === cfg.id}
                      >
                        {testingId === cfg.id
                          ? <><span className={styles.spinIcon} />测试中...</>
                          : <>
                              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                                <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>
                              </svg>连接测试
                            </>}
                      </button>

                      {/* 激活 */}
                      <button
                        className={`${styles.actBtn} ${styles.actToggle} ${cfg.is_active ? styles.isActive : ""}`}
                        onClick={() => !cfg.is_active && handleActivate(cfg)}
                        disabled={!!cfg.is_active || activatingId === cfg.id}
                      >
                        {cfg.is_active ? "已激活" : (activatingId === cfg.id ? "激活中..." : "激活")}
                      </button>

                      {/* 编辑 */}
                      {(() => {
                        const embeddingLocked = isEmbeddingEditLocked(cfg);
                        return (
                          <button
                            className={`${styles.actBtn} ${styles.actEdit}`}
                            onClick={() => { if (!embeddingLocked) { setEditing(cfg); setFormOpen(true); } }}
                            disabled={embeddingLocked}
                            title={embeddingLocked ? "已激活的 Embedding 配置涉及知识库索引，首期不支持直接修改" : undefined}
                          >
                            编辑
                          </button>
                        );
                      })()}

                      {/* 删除（二次确认）*/}
                      {(() => {
                        const embeddingLocked = isEmbeddingEditLocked(cfg);
                        return (
                          <button
                            className={`${styles.actBtn} ${styles.actDelete} ${deletingId === cfg.id ? styles.confirmDelete : ""}`}
                            onClick={() => { if (!embeddingLocked) handleDelete(cfg); }}
                            disabled={embeddingLocked}
                            title={embeddingLocked ? "已激活的 Embedding 配置涉及知识库索引，首期不支持直接删除" : undefined}
                          >
                            {deletingId === cfg.id ? "确认删除?" : "删除"}
                          </button>
                        );
                      })()}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <ModelForm
        open={formOpen}
        initial={editing}
        onClose={() => { setFormOpen(false); setEditing(null); }}
        onSuccess={() => { setFormOpen(false); setEditing(null); load(); }}
      />
    </div>
  );
}
