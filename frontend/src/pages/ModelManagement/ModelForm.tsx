/* ════════════════════════════════════════════
 *  模型配置表单 — 左侧 Tab + 右侧分区面板
 *  参考 DataAgent 设计实现
 * ════════════════════════════════════════════ */
import React, { useEffect, useRef, useState } from "react";
import { message } from "antd";
import {
  type ModelConfig,
  type ModelProtocol,
  type ModelType,
  type ModelConfigUpdate,
  addModelConfig,
  updateModelConfig,
} from "@/api/modelConfig";
import styles from "./ModelForm.module.css";
import {
  protocolForProvider,
  resolveModelTypeForProvider,
  isEmbeddingAllowed,
} from "./modelFormUtils";

interface Props {
  open: boolean;
  initial: ModelConfig | null;
  onClose: () => void;
  onSuccess: () => void;
}

/* ── 厂商默认配置 ──────────────────────────── */
const PROVIDER_DEFAULTS: Record<string, { label: string; baseUrl: string; abbr: string; cls: string; protocol: ModelProtocol }> = {
  deepseek:    { label: "DeepSeek",    baseUrl: "https://api.deepseek.com",                         abbr: "DS", cls: styles.logoDeepseek, protocol: "openai"    },
  qwen:        { label: "Qwen",        baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1", abbr: "Qw", cls: styles.logoQwen,     protocol: "openai"    },
  openai:      { label: "OpenAI",      baseUrl: "https://api.openai.com/v1",                        abbr: "AI", cls: styles.logoOpenai,   protocol: "openai"    },
  siliconflow: { label: "Siliconflow", baseUrl: "https://api.siliconflow.cn/v1",                    abbr: "Si", cls: styles.logoSilicon,  protocol: "openai"    },
  zhipu:       { label: "智谱 AI",    baseUrl: "https://open.bigmodel.cn/api/paas/v4",             abbr: "ZP", cls: styles.logoZhipu,    protocol: "openai"    },
  anthropic:   { label: "Anthropic",   baseUrl: "https://api.anthropic.com",                        abbr: "An", cls: styles.logoCustom,   protocol: "anthropic" },
  custom:      { label: "Custom",      baseUrl: "",                                                  abbr: "Cu", cls: styles.logoCustom,   protocol: "openai"    },
};

// protocolForProvider / resolveModelTypeForProvider / isEmbeddingAllowed
// 从 modelFormUtils 导入，便于独立单元测试

const PROVIDERS = Object.keys(PROVIDER_DEFAULTS);
const MASK = "****";

/* ── 初始表单状态 ──────────────────────────── */
const INIT: Omit<ModelConfig, "id" | "is_active" | "created_at" | "updated_at"> = {
  provider: "", protocol: "openai", base_url: "", api_key: "", model_name: "",
  model_type: "CHAT", temperature: 0.0, max_tokens: 12288,
  completions_path: "", embeddings_path: "",
  proxy_enabled: false, proxy_host: "", proxy_port: undefined, proxy_username: "", proxy_password: "",
};

type TabId = "basic" | "conn" | "run" | "proxy";
const TABS: { id: TabId; label: string; icon: React.ReactNode }[] = [
  { id: "basic", label: "基本信息", icon: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg> },
  { id: "conn",  label: "连接配置", icon: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg> },
  { id: "run",   label: "运行参数", icon: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33"/></svg> },
  { id: "proxy", label: "网络代理", icon: <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg> },
];

export default function ModelForm({ open, initial, onClose, onSuccess }: Props) {
  const isEdit = !!initial;
  const [form, setForm] = useState({ ...INIT });
  const [activeTab, setActiveTab] = useState<TabId>("basic");
  const [provOpen, setProvOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const panelRef = useRef<HTMLDivElement>(null);
  const scrollLock = useRef(false);

  /* 打开时初始化 */
  useEffect(() => {
    if (!open) return;
    setActiveTab("basic");
    setProvOpen(false);
    setForm(initial
      ? { ...INIT, ...initial, api_key: initial.api_key ?? "" }
      : { ...INIT });
  }, [open, initial]);

  const set = (k: keyof typeof form, v: unknown) => setForm((p) => ({ ...p, [k]: v }));

  /* 滚动 → 同步 Tab */
  const onPanelScroll = () => {
    if (scrollLock.current || !panelRef.current) return;
    const container = panelRef.current;
    const sections = container.querySelectorAll<HTMLElement>("[data-section]");
    let activeIdx = 0;
    sections.forEach((sec, i) => {
      if (sec.offsetTop - container.offsetTop <= container.scrollTop + 40) activeIdx = i;
    });
    if (container.scrollTop + container.clientHeight >= container.scrollHeight - 5)
      activeIdx = sections.length - 1;
    setActiveTab(TABS[activeIdx].id);
  };

  const scrollTo = (id: TabId) => {
    if (!panelRef.current) return;
    const sec = panelRef.current.querySelector<HTMLElement>(`[data-section="${id}"]`);
    if (!sec) return;
    scrollLock.current = true;
    setActiveTab(id);
    const top = sec.offsetTop - panelRef.current.offsetTop;
    panelRef.current.scrollTo({ top, behavior: "smooth" });
    setTimeout(() => { scrollLock.current = false; }, 500);
  };

  /* 厂商选择 */
  const selectProvider = (key: string) => {
    const d = PROVIDER_DEFAULTS[key];
    setForm((p) => ({
      ...p,
      provider: key,
      base_url: d.baseUrl || p.base_url,
      protocol: key === "custom" ? p.protocol : d.protocol,
      model_type: resolveModelTypeForProvider(key, p.model_type),
    }));
    setProvOpen(false);
  };

  /* 保存 */
  const handleSave = async () => {
    if (!form.provider) { message.error("请选择提供商"); return; }
    if (!form.model_name.trim()) { message.error("请输入模型名称"); return; }
    if (!form.api_key.trim()) { message.error("请输入 API 密钥"); return; }
    if (!form.base_url.trim()) { message.error("请输入 Base URL"); return; }
    setSaving(true);
    const protocol = protocolForProvider(form.provider, form.protocol as ModelProtocol);
    const payload = { ...form, protocol };
    try {
      if (isEdit) {
        await updateModelConfig({ ...payload, id: initial!.id! } as ModelConfigUpdate);
        message.success("更新成功");
      } else {
        await addModelConfig(payload);
        message.success("新增成功，可在列表中激活使用");
      }
      onSuccess();
    } catch (e: any) { message.error(e?.response?.data?.detail ?? "保存失败"); }
    finally { setSaving(false); }
  };

  if (!open) return null;

  /* 当前厂商 */
  const currentProvMeta = form.provider ? PROVIDER_DEFAULTS[form.provider] : null;

  return (
    <div className={styles.overlay} onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={styles.modal}>
        {/* 头部 */}
        <div className={styles.head}>
          <h3>{isEdit ? "编辑模型配置" : "新增模型配置"}</h3>
          <button className={styles.closeBtn} onClick={onClose}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>

        {/* 主体：左 Tab + 右面板 */}
        <div className={styles.body}>
          {/* 左侧 Tab */}
          <div className={styles.tabs}>
            {TABS.map((t) => (
              <div
                key={t.id}
                className={`${styles.tab} ${activeTab === t.id ? styles.tabActive : ""}`}
                onClick={() => scrollTo(t.id)}
              >
                {t.icon}<span>{t.label}</span>
              </div>
            ))}
          </div>

          {/* 右侧可滚动面板 */}
          <div className={styles.panels} ref={panelRef} onScroll={onPanelScroll}>
            {/* ── 基本信息 ── */}
            <div className={styles.section} data-section="basic">
              <div className={styles.sectionTitle}>基本信息</div>

              {/* 提供商 */}
              <div className={styles.row}>
                <label className={styles.rowLabel}>提供商 <span className={styles.req}>*</span></label>
                <div className={styles.rowCtrl} style={{ position: "relative" }}>
                  <div
                    className={`${styles.provSelect} ${provOpen ? styles.provSelectOpen : ""}`}
                    onClick={() => !isEdit && setProvOpen((v) => !v)}
                    style={isEdit ? { cursor: "not-allowed", opacity: 0.7 } : undefined}
                    title={isEdit ? "编辑时不可修改提供商" : undefined}
                  >
                    <div className={styles.provDisplay}>
                      {currentProvMeta
                        ? <><span className={`${styles.provLogo} ${currentProvMeta.cls}`}>{currentProvMeta.abbr}</span><span style={{ fontSize: 13, color: "#1a2332" }}>{currentProvMeta.label}</span></>
                        : <span className={styles.provPlaceholder}>请选择</span>}
                    </div>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#9ba5b2" strokeWidth="2"
                      style={{ transition: "transform .2s", transform: provOpen ? "rotate(180deg)" : "none" }}>
                      <polyline points="6 9 12 15 18 9"/>
                    </svg>
                  </div>
                  {provOpen && (
                    <div className={styles.provDropdown}>
                      {PROVIDERS.map((p) => {
                        const d = PROVIDER_DEFAULTS[p];
                        return (
                          <div key={p} className={`${styles.provOption} ${form.provider === p ? styles.provOptionActive : ""}`}
                            onClick={() => selectProvider(p)}>
                            <span className={`${styles.provLogo} ${d.cls}`}>{d.abbr}</span>
                            <span>{d.label}</span>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>

              {/* 协议 — 仅 custom 时显示，其余自动推导 */}
              {form.provider === "custom" && (
                <div className={styles.row}>
                  <label className={styles.rowLabel}>调用协议 <span className={styles.req}>*</span></label>
                  <div className={styles.rowCtrl}>
                    <div style={{ display: "flex", gap: 8 }}>
                      {(["openai", "anthropic"] as ModelProtocol[]).map((proto) => (
                        <label key={proto} style={{
                          display: "flex", alignItems: "center", gap: 6,
                          padding: "5px 12px", borderRadius: 8, cursor: "pointer",
                          border: `1.5px solid ${form.protocol === proto ? "#4f6ef7" : "#e5e8ed"}`,
                          background: form.protocol === proto ? "rgba(79,110,247,0.05)" : "#f5f7fa",
                          fontSize: 13, color: form.protocol === proto ? "#4f6ef7" : "#5a6878",
                        }}>
                          <input type="radio" name="mf_protocol" value={proto}
                            checked={form.protocol === proto}
                            onChange={() => set("protocol", proto)}
                            style={{ display: "none" }} />
                          {proto === "openai" ? "OpenAI 兼容" : "Anthropic"}
                        </label>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              {/* 模型名称 */}
              <div className={styles.row}>
                <label className={styles.rowLabel}>模型名称 <span className={styles.req}>*</span></label>
                <div className={styles.rowCtrl}>
                  <input type="text" placeholder="如: deepseek-chat / qwen-plus"
                    value={form.model_name} onChange={(e) => set("model_name", e.target.value)} />
                </div>
              </div>

              {/* 模型类型 */}
              <div className={`${styles.row} ${styles.rowTop}`}>
                <label className={styles.rowLabel} style={{ lineHeight: "28px", paddingTop: 4 }}>
                  模型类型 <span className={styles.req}>*</span>
                </label>
                <div className={styles.rowCtrl}>
                  <div className={styles.typeCards}>
                    {(["CHAT", "EMBEDDING"] as ModelType[]).map((t) => {
                      const isAnthropicEmbed = !isEmbeddingAllowed(form.provider) && t === "EMBEDDING";
                      const disabled = isEdit || isAnthropicEmbed;
                      return (
                        <label key={t}
                          className={`${styles.typeCard} ${form.model_type === t ? styles.typeCardSelected : ""} ${isAnthropicEmbed ? styles.typeCardDisabled : ""}`}
                          onClick={() => !disabled && set("model_type", t)}
                          title={isAnthropicEmbed ? "Anthropic 协议当前仅支持对话模型" : undefined}>
                          <input type="radio" name="mf_type" readOnly
                            checked={form.model_type === t}
                            style={{ position: "absolute", opacity: 0, width: 0, height: 0 }} />
                          <div className={`${styles.typeIcon} ${t === "CHAT" ? styles.typeIconChat : styles.typeIconEmbed}`}>
                            {t === "CHAT"
                              ? <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
                              : <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><circle cx="12" cy="12" r="3"/><path d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.48-8.48l2.83-2.83M2 12h4m12 0h4M4.93 4.93l2.83 2.83m8.48 8.48l2.83 2.83"/></svg>}
                          </div>
                          <div className={styles.typeInfo}>
                            <span className={styles.typeName}>{t === "CHAT" ? "对话模型" : "嵌入模型"}</span>
                          </div>
                          <div className={`${styles.typeCheck} ${form.model_type === t ? styles.typeCheckSelected : ""}`}>
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><polyline points="20 6 9 17 4 12"/></svg>
                          </div>
                        </label>
                      );
                    })}
                  </div>
                  {!isEmbeddingAllowed(form.provider) && (
                    <p style={{ fontSize: 11, color: "#9ba5b2", marginTop: 4 }}>Anthropic 协议当前仅支持对话模型</p>
                  )}
                  {isEdit && <p style={{ fontSize: 11, color: "#9ba5b2", marginTop: 4 }}>编辑时不可修改模型类型</p>}
                </div>
              </div>
            </div>

            {/* ── 连接配置 ── */}
            <div className={styles.section} data-section="conn">
              <div className={styles.sectionTitle}>连接配置</div>

              <div className={styles.row}>
                <label className={styles.rowLabel}>API 密钥 <span className={styles.req}>*</span></label>
                <div className={styles.rowCtrl}>
                  <input type="password" id="mf_apikey" placeholder={isEdit ? "不修改则填 ****" : "sk-xxxxxxxx"}
                    value={form.api_key} onChange={(e) => set("api_key", e.target.value)} />
                </div>
              </div>

              <div className={styles.row}>
                <label className={styles.rowLabel}>
                  Base URL <span className={styles.req}>*</span>
                  <span className={styles.tooltip} data-tip="兼容 OpenAI 协议的 Base URL">?</span>
                </label>
                <div className={styles.rowCtrl}>
                  <input type="text" placeholder="https://api.example.com"
                    value={form.base_url} onChange={(e) => set("base_url", e.target.value)} />
                </div>
              </div>

              <div className={styles.row}>
                <label className={styles.rowLabel}>
                  {form.model_type === "EMBEDDING" ? "Embeddings 路径" : "Completions 路径"}
                </label>
                <div className={styles.rowCtrl}>
                  <input type="text"
                    placeholder={form.model_type === "EMBEDDING" ? "/v1/embeddings" : "/v1/chat/completions"}
                    value={form.model_type === "EMBEDDING" ? (form.embeddings_path ?? "") : (form.completions_path ?? "")}
                    onChange={(e) => set(form.model_type === "EMBEDDING" ? "embeddings_path" : "completions_path", e.target.value)} />
                </div>
              </div>
            </div>

            {/* ── 运行参数 ── */}
            <div className={styles.section} data-section="run">
              <div className={styles.sectionTitle}>运行参数</div>

              {form.model_type === "CHAT" ? (
                <>
                  <div className={styles.row}>
                    <label className={styles.rowLabel}>
                      温度
                      <span className={styles.tooltip} data-tip="控制随机性，0 最确定，2 最随机">?</span>
                    </label>
                    <div className={styles.rowCtrl}>
                      <div className={styles.numGroup}>
                        <button className={styles.stepBtn} type="button"
                          onClick={() => set("temperature", Math.max(0, +((form.temperature ?? 0) - 0.1).toFixed(1)))}>−</button>
                        <input type="number" min={0} max={2} step={0.1} className={styles.numInput}
                          value={form.temperature ?? 0}
                          onChange={(e) => set("temperature", parseFloat(e.target.value) || 0)} />
                        <button className={styles.stepBtn} type="button"
                          onClick={() => set("temperature", Math.min(2, +((form.temperature ?? 0) + 0.1).toFixed(1)))}>+</button>
                      </div>
                    </div>
                  </div>

                  <div className={styles.row}>
                    <label className={styles.rowLabel}>
                      最大 Token
                      <span className={styles.tooltip} data-tip="单次请求生成文本的最大长度">?</span>
                    </label>
                    <div className={styles.rowCtrl}>
                      <div className={styles.numGroup}>
                        <button className={styles.stepBtn} type="button"
                          onClick={() => set("max_tokens", Math.max(1, (form.max_tokens ?? 12288) - 100))}>−</button>
                        <input type="number" min={1} className={styles.numInput}
                          value={form.max_tokens ?? 12288}
                          onChange={(e) => set("max_tokens", parseInt(e.target.value) || 12288)} />
                        <button className={styles.stepBtn} type="button"
                          onClick={() => set("max_tokens", (form.max_tokens ?? 12288) + 100)}>+</button>
                      </div>
                    </div>
                  </div>
                </>
              ) : (
                <p className={styles.embedHint}>嵌入模型无需配置温度和 Token 参数。</p>
              )}
            </div>

            {/* ── 网络代理 ── */}
            <div className={styles.section} data-section="proxy">
              <div className={styles.sectionTitle}>网络代理配置</div>

              <div className={styles.row}>
                <label className={styles.rowLabel}>启用代理</label>
                <div className={styles.rowCtrl}>
                  <div className={styles.proxyToggleRow}>
                    <label className={styles.toggleSwitch}>
                      <input type="checkbox" checked={form.proxy_enabled}
                        onChange={(e) => set("proxy_enabled", e.target.checked)} />
                      <span className={styles.toggleTrack}>
                        <span className={styles.toggleThumb} />
                      </span>
                    </label>
                    <span className={styles.proxyHint}>如服务器处于受限内网，请开启代理连接 AI 服务</span>
                  </div>
                </div>
              </div>

              {form.proxy_enabled && (
                <>
                  <div className={styles.row}>
                    <label className={styles.rowLabel}>代理主机 <span className={styles.req}>*</span></label>
                    <div className={styles.rowCtrl}>
                      <input type="text" placeholder="127.0.0.1 或 proxy.example.com"
                        value={form.proxy_host ?? ""} onChange={(e) => set("proxy_host", e.target.value)} />
                    </div>
                  </div>
                  <div className={styles.row}>
                    <label className={styles.rowLabel}>代理端口 <span className={styles.req}>*</span></label>
                    <div className={styles.rowCtrl}>
                      <input type="number" placeholder="7890" min={1} max={65535}
                        value={form.proxy_port ?? ""} onChange={(e) => set("proxy_port", parseInt(e.target.value) || undefined)} />
                    </div>
                  </div>
                  <div className={styles.row}>
                    <label className={styles.rowLabel}>用户名</label>
                    <div className={styles.rowCtrl}>
                      <input type="text" placeholder="可选"
                        value={form.proxy_username ?? ""} onChange={(e) => set("proxy_username", e.target.value)} />
                    </div>
                  </div>
                  <div className={styles.row}>
                    <label className={styles.rowLabel}>代理密码</label>
                    <div className={styles.rowCtrl}>
                      <input type="password" placeholder="可选"
                        value={form.proxy_password ?? ""} onChange={(e) => set("proxy_password", e.target.value)} />
                    </div>
                  </div>
                </>
              )}
            </div>

          </div>
        </div>

        {/* 底部 */}
        <div className={styles.foot}>
          <button className={styles.footBtnCancel} onClick={onClose}>取消</button>
          <button className={styles.footBtnSave} onClick={handleSave} disabled={saving}>
            {saving
              ? "保存中..."
              : <><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="20 6 9 17 4 12"/></svg>{isEdit ? "保存修改" : "创建"}</>}
          </button>
        </div>
      </div>
    </div>
  );
}
