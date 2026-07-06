"""
Self-Evolution Data Agent — 配置中心
所有环境变量集中管理, 零散读取是架构的癌症
"""

from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings

# pydantic-settings env_file 相对 CWD 解析 — 固化为 config.py 所在目录的 ../.env
# (backend/app/config.py → backend/.env), 无论从项目根还是 backend/ 启动行为一致
_ENV_FILE = str(Path(__file__).resolve().parents[1] / ".env")


class Settings(BaseSettings):
    model_config = {"env_prefix": "IS_", "env_file": _ENV_FILE, "extra": "ignore"}

    # ── 元数据库 ──
    metadata_db_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/self_evolution_data_agent"
    metadata_pool_size: int = 20
    metadata_pool_max_overflow: int = 30
    metadata_pool_timeout_secs: int = 60

    # ── 时区 ──
    app_timezone: str = "Asia/Shanghai"  # IS_APP_TIMEZONE env

    @field_validator("app_timezone")
    @classmethod
    def _app_timezone_is_valid_iana(cls, v: str) -> str:
        from zoneinfo import available_timezones

        if v not in available_timezones():
            raise ValueError(f"非法 IANA 时区名: {v!r}")
        return v

    # ── ChromaDB ──
    chroma_persist_dir: str = "./data/chroma"

    # ── Git ──
    git_clone_dir: str = "./data/repos"
    git_token: str = ""  # 私有仓库访问令牌 (GitHub PAT / GitLab token / Gitee token)

    # ── 查询 ──
    query_row_limit: int = 1000  # noqa: hardcode
    render_row_limit: int = 20000  # noqa: hardcode
    """渲染源 (plan 末步 mode=render) 专用行上限; 与保护 LLM 上下文的 query_row_limit 分离.
    字节数学: 20000 行聚合数据 ≈2MB, 请求级作用域 (tool_trace 随请求结束 GC)."""
    probe_row_limit: int = 10  # noqa: hardcode
    """probe mode (小样本探查) 行上限; 三驱动 (mysql/oracle/mongo) 共用单一真相源,
    取代原各 driver probe 分支散落的 hardcode 10. 默认 10 保持原语义."""
    agent_tool_result_max_chars: int = 500_000  # noqa: hardcode
    """回喂 LLM 的单条 tool 结果字符预算; 超出则 dict-aware 收缩 + 截断 (tool_trace 不受影响)"""

    # ── Decomposer Routing ──
    # True (默认):  新路径 — Decomposer → PreQuery → ClarifyQuestion → 单集合执行
    # False (回滚): 沿用原 _select_datasource → engine.generate_query 路径 (regex 路由对中文短路)
    # P0-P5 全量交付 + 业务词典回灌就绪后默认启用; .env 可显式覆盖回 false 用作灰度回滚
    enable_decomposer_routing: bool = True
    # PreQuery 爆集守护阈值 — 候选 > 此值 → overflow=True, 前端提示"缩小范围"
    prequery_overflow_threshold: int = 20
    # pending_clarifications 的 TTL 小时数
    pending_ttl_hours: int = 24
    # pending_clarifications 清理任务轮询间隔 (秒)
    pending_cleanup_interval_secs: int = 3600  # noqa: hardcode

    # ── Knowledge Layer (Stage 1) ─────────────────────────
    knowledge_retrieve_critical_n: int = 5
    """retrieve_layer3 critical tier 召回上限"""
    knowledge_retrieve_normal_n: int = 5
    """retrieve_layer3 normal tier 召回上限"""
    knowledge_retrieve_default_k: int = 5
    """lookup_knowledge tool 默认 k 值"""
    knowledge_content_max_bytes: int = 8192  # noqa: hardcode
    """knowledge_entries.content 长度上限"""
    recall_payload_max_list_len: int = 8
    """召回 payload 内 list (非逻辑容器) 的元素数上限, 超出整体替换为 placeholder"""
    recall_payload_max_str_len: int = 120  # noqa: hardcode
    """召回 payload 内 str 字面量长度上限, 超出截断 + <+K chars> 标记"""
    recall_payload_long_scalar_len: int = 30
    """召回 payload 内 scalar 视为"长字面量"(如 ObjectId/UUID/timestamp) 的阈值"""

    # ── Audit Loop (Stage 1) ─────────────────────────────
    audit_proposed_max_age_days: int = 30
    """proposed 知识超此天数自动转 rejected (Stage 3 才接入后台任务, 配置先就位)"""
    audit_log_retention_days: int = 365  # noqa: hardcode
    """audit_log 冷归档前保留期"""
    audit_auto_expire_check_interval_hours: int = 24
    """proposed 自动过期后台任务运行频率 (Stage 3 Task 8)"""
    knowledge_hard_delete_enabled: bool = False
    """物理删除开关 (Stage 3 才用, 配置先就位)"""
    knowledge_edit_min_role: str = "admin"
    """编辑 canonical 的最低角色"""
    audit_page_size_default: int = 20
    """Stage 3 审核 API 默认分页大小 (queue/log 等)"""
    audit_page_size_max: int = 200  # noqa: hardcode
    """Stage 3 审核 API 单页上限, 防内存爆"""

    # ── Bulk Operations (Stage 1) ────────────────────────
    bulk_op_dry_run_default: bool = True
    """所有破坏性批量操作默认 dry-run"""
    bulk_op_backup_retention_days: int = 30
    """批量操作 SQL dump backup 保留天数"""
    bulk_op_require_confirm_above: int = 100  # noqa: hardcode
    """影响条目数超此值, 必须二次 confirm token"""

    # ── Phase 0 数据治理 (Stage 1 升级) ──────────────────
    legacy_relabel_batch_size: int = 10
    """Phase 0 LLM 重打标批大小, env: IS_LEGACY_RELABEL_BATCH_SIZE"""

    # ── Phase 1 Terminology Intake ───────────────────────
    terminology_term_max_len: int = 20
    """terminology term 字数上限（中文业务名词，通常 ≤10 字），env: IS_TERMINOLOGY_TERM_MAX_LEN"""

    terminology_synonym_max_len: int = 50
    """synonym 字数上限（释义性短语，英文术语天然更长），env: IS_TERMINOLOGY_SYNONYM_MAX_LEN"""

    example_result_summary_max_len: int = 300  # noqa: hardcode
    """示例查询知识 result_summary 字数上限（前端/后端/LLM prompt 统一真相源）,
    env: IS_EXAMPLE_RESULT_SUMMARY_MAX_LEN"""

    # ── Phase 1 schema-knowledge-onboarding 配置 ─────────
    promote_batch_size: int = 200
    """候选 promote 批大小, env: IS_PROMOTE_BATCH_SIZE"""
    candidate_retention_days: int = 90
    """候选保留天数, env: IS_CANDIDATE_RETENTION_DAYS"""
    candidate_rejected_retention_days: int = 30
    """rejected 候选保留天数, env: IS_CANDIDATE_REJECTED_RETENTION_DAYS"""
    relationship_join_hit_threshold: int = 5
    """B 维度 JOIN 命中阈值 N, env: IS_RELATIONSHIP_JOIN_HIT_THRESHOLD"""
    relationship_join_mapper_threshold: int = 2
    """B 维度 mapper 阈值 M, env: IS_RELATIONSHIP_JOIN_MAPPER_THRESHOLD"""
    usage_implicit_id_ref_hit_threshold: int = 3
    """隐式 ID 引用命中阈值, env: IS_USAGE_IMPLICIT_ID_REF_HIT_THRESHOLD"""
    extraction_failure_retry_max: int = 3
    """抽取失败最大重试次数, env: IS_EXTRACTION_FAILURE_RETRY_MAX"""
    nl_paraphrases_per_example: int = 5
    """H 维度 NL paraphrases 生成数量, env: IS_NL_PARAPHRASES_PER_EXAMPLE"""
    promote_lock_timeout_secs: int = 30
    """ns-level promote 锁超时, env: IS_PROMOTE_LOCK_TIMEOUT_SECS"""
    dynamic_sql_max_branches: int = 32
    """动态 SQL 最大分支数, env: IS_DYNAMIC_SQL_MAX_BRANCHES"""
    # ── Phase 2 修订 #6 EXPLAIN 闸门并发 ──
    explain_gate_concurrency: int = 4
    """同时跑的 EXPLAIN 数, env: IS_EXPLAIN_GATE_CONCURRENCY"""
    explain_gate_timeout_secs: int = 10
    """单次 EXPLAIN 超时, env: IS_EXPLAIN_GATE_TIMEOUT_SECS"""
    explain_gate_batch_interval_ms: int = 50
    """批间间隔, env: IS_EXPLAIN_GATE_BATCH_INTERVAL_MS"""

    # ── equivalence registry LLM 预算 ──
    equivalence_llm_budget_per_batch: int = 20
    """单批 promote 中 semantic_llm checker 最大 LLM 调用次数, env: IS_EQUIVALENCE_LLM_BUDGET_PER_BATCH"""
    equivalence_llm_timeout_secs: int = 8
    """semantic_llm checker 单次 LLM 调用超时, env: IS_EQUIVALENCE_LLM_TIMEOUT_SECS"""

    # ── schema-canonical-v2 audit log 分页 ──
    schema_audit_log_page_default: int = 100  # noqa: hardcode
    """schema canonical audit_log 端点默认每页条数, env: IS_SCHEMA_AUDIT_LOG_PAGE_DEFAULT"""
    schema_audit_log_page_max: int = 1000  # noqa: hardcode
    """schema canonical audit_log 端点单页上限, env: IS_SCHEMA_AUDIT_LOG_PAGE_MAX"""

    # ── P0 对话体验 — Session ──
    session_list_max: int = 50  # noqa: hardcode
    """会话列表端点单页上限 (按 updated_at DESC), env: IS_SESSION_LIST_MAX"""

    # ── Phase 2 Terminology Refresh (trainer 末端异步 worker) ──
    terminology_refresh_timeout_secs: int = 300  # noqa: hardcode
    """Phase 2 refresh_namespace_terminology 单 namespace 整体超时, env: IS_TERMINOLOGY_REFRESH_TIMEOUT_SECS"""

    # ── Phase 2 Enum Field Sampling ──
    field_sample_default_limit: int = 50  # noqa: hardcode
    """inspect_samples 默认采样数, env: IS_FIELD_SAMPLE_DEFAULT_LIMIT"""
    field_sample_max_limit: int = 500  # noqa: hardcode
    """inspect_samples 最大采样数, env: IS_FIELD_SAMPLE_MAX_LIMIT"""

    # ── Phase 2 Enum Reverse Sync Worker ──
    enum_sync_batch_size: int = 50  # noqa: hardcode
    """enum_sync_loop 单 tick 处理任务上限, env: IS_ENUM_SYNC_BATCH_SIZE"""
    enum_sync_interval_secs: int = 5  # noqa: hardcode
    """enum_sync_loop tick 间隔秒, env: IS_ENUM_SYNC_INTERVAL_SECS"""

    # ── Phase 4 Knowledge Loader (agent_loop 主链路单一入口) ──
    knowledge_route_hint_inject_k: int = 5
    """route_hint 注入 prompt 的数量上限, env: IS_KNOWLEDGE_ROUTE_HINT_INJECT_K"""
    knowledge_terminology_inject_k: int = 0
    """terminology 注入 prompt 的数量上限 (0 表示全量, 不裁剪), env: IS_KNOWLEDGE_TERMINOLOGY_INJECT_K"""
    knowledge_loader_timeout_secs: int = 10
    """load_all_knowledge 整体超时秒数 (critical SQL + vector retrieve 总和), env: IS_KNOWLEDGE_LOADER_TIMEOUT_SECS"""

    # ── Agent Loop Tools (Stage 4) ───────────────────────
    agent_learn_source: str = "agent_learn"
    """save_knowledge tool 写入时的 source 字段值"""

    # ── Stage 2 抓手 A: HyQE ──────────────────────────
    hypothetical_queries_per_entry: int = 3
    """rule / route_hint 入库时 LLM 生成的 hypothetical query 数量上限."""
    hypothetical_queries_llm_timeout_secs: int = 10
    """单次生成调用超时 (chat_completion 内部超时由 LLM 客户端管, 此为软上限文档值)."""
    hypothetical_queries_llm_temperature: float = 0.3
    """HQ 生成 LLM temperature (IS_HYPOTHETICAL_QUERIES_LLM_TEMPERATURE)."""
    hypothetical_queries_llm_max_tokens: int = 1024  # noqa: hardcode
    """HQ 生成 LLM max_tokens (IS_HYPOTHETICAL_QUERIES_LLM_MAX_TOKENS)."""
    hypothetical_queries_enabled: bool = True
    """灰度开关. 关闭 → upsert 退化为单向量 (与现状一致)."""
    hq_question_max_len: int = 80
    """HQItem.q 最大字符数 (IS_HQ_QUESTION_MAX_LEN)."""
    hq_covered_path_max: int = 10
    """HQItem.covered_path 最大长度 (IS_HQ_COVERED_PATH_MAX)."""

    hq_text_validation_mode: str = "lenient"
    """HQ 文本子串校验严格度: strict / lenient / off (IS_HQ_TEXT_VALIDATION_MODE)."""

    # ── Stage 2 抓手 C: Self-RAG reflection ──────────
    agent_reflection_enabled: bool = True
    """开关. 关闭 = build_system_prompt 不加 reflection 段, agent 不输出 reflection."""

    # ── Stage 2 抓手 E: agent_traces ─────────────────
    agent_trace_retention_days: int = 365  # noqa: hardcode
    """trace 行 retention (cleanup cron 每天清超期行)."""
    agent_trace_refine_batch_max: int = 50
    """单次批量提炼上限."""
    agent_trace_refine_llm_timeout_secs: int = 60
    """批量提炼单次 LLM 调用超时."""
    llm_client_timeout_secs: int = 120  # noqa: hardcode
    """LLM HTTP 客户端超时 (主链路 chat_completion_with_tools). env: IS_LLM_CLIENT_TIMEOUT_SECS.
    默认 120s: 多轮查询上下文累积 (schema + 多次结果行) 后 LLM 响应远超旧 15s 硬编码."""

    llm_connect_test_timeout_secs: int = 15  # noqa: hardcode
    """LLM 连接测试 (model_config 测试连接按钮, 一次性 Hello ping) 超时.
    env: IS_LLM_CONNECT_TEST_TIMEOUT_SECS.
    默认 15s: ping 性质短超时, 与主链路 120s 区分, 死库不被拖 120s."""

    agent_trace_compact_row_cap: int = 20  # noqa: hardcode
    """落库 trace_json 结构裁剪时, 单工具大数组保留行数上限. env: IS_AGENT_TRACE_COMPACT_ROW_CAP.
    触发条件: 序列化后字节数 > agent_trace_max_json_bytes. 裁 execute_query.rows /
    inspect_values.values / lookup_knowledge 三类大数组."""

    agent_trace_max_json_bytes: int = 200_000  # noqa: hardcode
    """trace_json 落库体积预算线 (字节). env: IS_AGENT_TRACE_MAX_JSON_BYTES.
    序列化后 len(s.encode()) > 此值 → 触发 compact_tool_trace_for_storage 结构裁剪
    (三类大数组截到 agent_trace_compact_row_cap 行), 裁后不再 [:N] 硬切, 保证产出合法 JSON."""

    llm_max_tokens_default: int = 12288  # noqa: hardcode
    """LLM 单次响应 max_tokens 统一兜底默认 (Web UI 未按模型显式设值时使用).
    env: IS_LLM_MAX_TOKENS_DEFAULT. model_registry 构建 chat 参数时 row.max_tokens or 此值."""

    enum_extract_max_tokens: int = 4096  # noqa: hardcode
    """enum 类专精解析单次 LLM 调用 max_tokens. env: IS_ENUM_EXTRACT_MAX_TOKENS.
    enum 定义体积有界, 独立于主链路 max_tokens 默认."""

    # ── Stage 2 抓手 B: 召回反馈环 + 衰减 ──────────────
    kb_decay_recall_threshold: int = 10
    """衰减规则 1 触发的最低累计召回次数 (低于此不参与采纳率判定)."""
    kb_decay_adoption_ratio: float = 0.1
    """衰减规则 1 阈值 — adopted/recall < 此值 → superseded."""
    kb_decay_stale_days: int = 90
    """衰减规则 2 阈值 — last_recalled_at 早于此天数 → superseded."""
    kb_decay_check_interval_hours: int = 24
    """衰减 cron 周期."""

    # ── Stage 2 抓手 D: A-MEM 演化 ────────────────────
    amem_neighbor_k: int = 5
    """入库时检索的语义近邻数."""
    amem_llm_timeout_secs: int = 15
    """detect_relations LLM 调用软上限文档值."""
    amem_enabled: bool = True
    """灰度开关. 关闭 → save_knowledge 不写 related_entry_ids_json."""

    inspect_field_default_sample: int = 5
    """inspect_field_values 默认取样数 (Stage 4 Task 4)"""
    query_cost_single_layer_limit: int = 50_000  # noqa: hardcode
    """estimate_query_cost 单层扫描告警阈值, 超此值返 warning='single_layer_overflow'"""
    query_cost_total_limit: int = 5_000_000  # noqa: hardcode
    """全链路累计扫描硬上限 (后续 plan_executor 决策, 此处仅声明)"""
    query_cost_default_batch_size: int = 500  # noqa: hardcode
    """execute_batched_aggregate 默认 batch 大小"""
    clarify_wait_timeout_secs: int = 600  # noqa: hardcode

    # ── 异步知识抽取阈值 ──
    knowledge_extract_min_tool_calls: int = 5
    """end_turn 异步抽取最低 tool_count 门槛. env: IS_KNOWLEDGE_EXTRACT_MIN_TOOL_CALLS"""
    knowledge_extract_per_call_max_chars: int = 500
    """tool_trace 摘要每条 tool call 最大字符. env: IS_KNOWLEDGE_EXTRACT_PER_CALL_MAX_CHARS"""
    """clarify_with_user tool 等用户答的最长时间, 超时返 timeout=True"""

    enable_agent_loop: bool = True
    """Stage 4 灰度回滚开关. True 走 agent loop, False 回退 execute_query (Task 12 删)"""

    # ── Agent Loop (Stage 4 + 2026-05-12 配额分类化) ─────
    agent_loop_iteration_limit_enabled: bool = True
    """是否启用迭代配额. False 时 3 桶配额全部 sys.maxsize 处理,
    仅 dead_loop / cancel / token 三道兜底神圣不可关.
    env: IS_AGENT_LOOP_ITERATION_LIMIT_ENABLED, 默认 True."""

    agent_loop_max_exploratory_calls: int = 25  # noqa: hardcode
    """探索类 tool (lookup/schema/inspect/prequery/cost_estimate) 累计上限.
    env: IS_AGENT_LOOP_MAX_EXPLORATORY_CALLS, 默认 25."""

    agent_loop_max_decisive_calls: int = 15  # noqa: hardcode
    """决策类 tool (count_only/batched/plan/execute/chart) 累计上限.
    env: IS_AGENT_LOOP_MAX_DECISIVE_CALLS, 默认 15."""

    agent_loop_max_total_iterations: int = 40  # noqa: hardcode
    """主循环总迭代上限, 防 explore+decisive 都不触顶但 LLM 反复横跳烧钱.
    env: IS_AGENT_LOOP_MAX_TOTAL_ITERATIONS, 默认 40."""
    agent_loop_max_tool_concurrency: int = 5
    """单轮并发执行 tool_calls 的信号量上限 (防 DB/ChromaDB 连接池被打爆)"""
    agent_loop_dead_loop_window: int = 3
    """死循环检测窗口 — 最近 N 次同名+同参 tool_call 触发升级 clarify"""

    # ── Error_Class 重复 → Forced_Clarify (mongo-flavor-capabilities-and-error-clarify) ──
    agent_loop_error_class_window_size: int = 5
    """Error_Class_Window 滑动窗口大小 (最近 N 次工具结果).
    env: IS_AGENT_LOOP_ERROR_CLASS_WINDOW_SIZE, 默认 5."""
    agent_loop_error_class_threshold: int = 2
    """同一 Error_Class 在窗口内达此次数 → 触发 Forced_Clarify.
    env: IS_AGENT_LOOP_ERROR_CLASS_THRESHOLD, 默认 2."""
    agent_loop_max_forced_clarify_per_class: int = 1
    """单次运行内同一 Error_Class 触发 Forced_Clarify 的次数上限.
    env: IS_AGENT_LOOP_MAX_FORCED_CLARIFY_PER_CLASS, 默认 1."""
    agent_loop_error_class_msg_signature_len: int = 80  # noqa: hardcode
    """无数值码错误的消息特征截断长度 (normalize_error_class 末位兜底).
    env: IS_AGENT_LOOP_ERROR_CLASS_MSG_SIGNATURE_LEN, 默认 80."""

    agent_cancel_grace_secs: float = 5.0  # noqa: hardcode
    """POST /cancel 后等 worker 清理的最大秒数, 超时强制放弃"""

    # ── LLM Retry (P1-14) ────────────────────────────────
    llm_retry_max: int = 1
    """LLM transient 错误 (5xx / TimeoutError / ConnectionError) 的最大重试次数
    (env: IS_LLM_RETRY_MAX, 默认 1).

    4xx 不重试 (业务错误); 0 表示完全禁用 retry.
    """

    agent_loop_context_limit_tokens: int = 80_000
    """agent_loop messages 累积 token 硬阈值 (env: IS_AGENT_LOOP_CONTEXT_LIMIT_TOKENS, 默认 80K).

    P1-14 决策: 不实施"上下文压缩"策略 (设计承诺 context_compress, 现状不需要 —
    max_total_iterations=40 × 3K avg 仍远不到 80K). 到达硬阈值直接 abort 而非沉默累积,
    fail-fast 让用户感知需调整问题或拆分会话, 而非看到错误摘要.
    """

    # ── LLM 思考模式全局控制 ──
    llm_thinking_enabled: bool = False
    """全局思考模式开关 (默认关), env: IS_LLM_THINKING_ENABLED.

    True 时仅对显式传 thinking=True 的调用方生效 (OpenAI: 不注入 disabled;
    Claude: 传 enabled + budget_tokens). 绝大多数调用方是确定性抽取/判定,
    默认关可避免 DeepSeek 思考耗尽 token 预算导致空响应.
    """

    llm_claude_thinking_budget_tokens: int = 4000  # noqa: hardcode
    """Claude extended thinking 最小 token 预算, env: IS_LLM_CLAUDE_THINKING_BUDGET_TOKENS.

    Anthropic 要求 >=1024, 默认 4000 为推荐最小值.
    """

    # ── SSE Keepalive (Stage 5) ──────────────────────────
    agent_keepalive_interval_secs: int = 30  # noqa: hardcode
    """SSE 心跳发送间隔, 防客户端连接超时"""

    # ── Code Parser (Stage 2 Task 8 — hardcode 治理搬 settings) ──
    code_parse_batch_char_limit: int = 48000  # noqa: hardcode
    """LLM 全量解析每批累计字符上限 (~18K token)"""
    code_parse_max_file_chars: int = 16000  # noqa: hardcode
    """单文件解析输入截断上限"""
    code_parse_llm_max_tokens: int = 16384  # noqa: hardcode
    """code_parser LLM 输出 max_tokens (实测 output ~6.6K, 16K 富余 2.5x)"""
    code_parse_slim_threshold: int = 8000  # noqa: hardcode
    """ref 文件超过此字符量剥离方法体, 仅留签名"""

    # ── Code Parser 多轮分层展开 (2026-05-23 spec, 复杂 Entity 504 治理) ──
    code_parse_complex_threshold: int = 25  # noqa: hardcode
    """batch 总复杂度阈值, 超过则走多轮路径; 经 16384 max_tokens 实测调优"""
    code_parse_round1_max_tokens: int = 4096  # noqa: hardcode
    """Round 1 骨架抽取 max_tokens (顶层字段 + types_to_expand, 输出体小)"""
    code_parse_round2_max_tokens: int = 8192  # noqa: hardcode
    """Round 2 类型展开 max_tokens (每批 5 个类的完整 sub_fields)"""
    code_parse_round2_classes_per_call: int = 5  # noqa: hardcode
    """Round 2 单次 LLM 调用展开的类数, 经验上 5 个能在 50s 内返"""
    code_parse_round2_classes_per_call_fallback: int = 3  # noqa: hardcode
    """Round 2 超时降级后的类数 (5 → 3 重试一次)"""
    code_parse_expansion_max_depth: int = 4  # noqa: hardcode
    """_fill_sub_fields 递归深度上限, 防自引用爆炸 + 控制输出体"""

    # ── Evaluator (Stage 2 Task 8) ──
    evaluator_max_tokens_first: int = 8192  # noqa: hardcode
    """evaluator 首次评估 max_tokens"""
    evaluator_max_tokens_retry: int = 16384  # noqa: hardcode
    """evaluator 截断重试 max_tokens"""

    # ── Knowledge Intake (Stage 2 Task 8) ──
    knowledge_normal_max_chars: int = 2000  # noqa: hardcode
    """tier=normal 原文上限 (prompt 提示 + 前端校验, 模块不硬截断)"""

    # ── DataSource 连接探测 (trainer snapshot 刷新) ──
    datasource_connect_timeout_ms: int = 5000  # noqa: hardcode
    """训练时实时连接 DataSource 的超时毫秒数 (MongoDB serverSelectionTimeoutMS / MySQL connect_timeout 换算)"""

    # ── Driver Pool (Stage 1) ────────────────────────────
    mysql_pool_max_size: int = 5
    """aiomysql 连接池上限. env: IS_MYSQL_POOL_MAX_SIZE"""
    mysql_pool_timeout_secs: int = 10
    """获取连接超时. env: IS_MYSQL_POOL_TIMEOUT_SECS"""
    mysql_query_timeout_secs: int = 30
    """单 SQL 执行超时. env: IS_MYSQL_QUERY_TIMEOUT_SECS"""
    mongo_pool_max_size: int = 100  # noqa: hardcode
    """motor maxPoolSize. env: IS_MONGO_POOL_MAX_SIZE"""
    mongo_connect_timeout_ms: int = 5000  # noqa: hardcode — IS_MONGO_CONNECT_TIMEOUT_MS — 建源/画像探测超时

    # ── Oracle Driver Pool ────────────────────────────────
    # Thin mode (默认): 不需要 Oracle Instant Client, 支持 Oracle 12.1+.
    # Thick mode: 需要 Oracle Instant Client; 支持 Oracle 11g+.
    #   - 设置 IS_ORACLE_THICK_MODE_LIB_DIR 为 Instant Client 目录路径启用.
    #   - 例: /opt/oracle/instantclient_21_1 (Linux) 或 /usr/local/oracle (macOS x86).
    oracle_thick_mode_lib_dir: str = ""
    """Oracle Instant Client 目录 (非空则启用 Thick mode). env: IS_ORACLE_THICK_MODE_LIB_DIR"""

    # min=0: 启动时不预建连接, 按需增长; 生产如需预热可改为 1.
    oracle_pool_min_size: int = 0
    """oracledb AsyncConnectionPool min. env: IS_ORACLE_POOL_MIN_SIZE"""
    oracle_pool_max_size: int = 5
    """oracledb AsyncConnectionPool max. env: IS_ORACLE_POOL_MAX_SIZE"""
    oracle_pool_increment: int = 1
    """oracledb AsyncConnectionPool increment. env: IS_ORACLE_POOL_INCREMENT"""
    oracle_pool_timeout_secs: int = 10
    """等待连接超时 (pool wait_timeout = × 1000 ms). env: IS_ORACLE_POOL_TIMEOUT_SECS"""
    oracle_query_timeout_secs: int = 30
    """单 SQL 执行超时. env: IS_ORACLE_QUERY_TIMEOUT_SECS"""
    oracle_connect_timeout_secs: int = 10
    """TCP 握手超时 (tcp_connect_timeout). env: IS_ORACLE_CONNECT_TIMEOUT_SECS"""

    # ── Langfuse 追踪 ──
    # 方式 1: 自部署 — docker compose -f docker-compose.langfuse.yml up -d
    #   IS_LANGFUSE_HOST=http://localhost:3001
    # 方式 2: Langfuse Cloud (无需 Docker) — https://cloud.langfuse.com 注册后获取 key
    #   IS_LANGFUSE_HOST=https://cloud.langfuse.com
    # 留空则禁用追踪, 不影响主流程
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_flush_at: int = 32  # noqa: hardcode
    """OTLP BatchSpanProcessor 每批导出的 span 上限 (默认 64, SDK 原始默认 512)"""
    langfuse_timeout: int = 10  # noqa: hardcode
    """OTLP HTTP 请求超时秒数 (默认 10, SDK 原始默认 5, 跨洲网络需更长)"""
    langfuse_debug_payload_size: bool = False
    """开启后, OTLP 导出前打印每个 span 的 input/output payload 字节数 (诊断超时根因用)"""

    # ── JWT 认证 ──
    jwt_secret: str = "change-me-in-production"
    jwt_expire_hours: int = 24

    # ── 密码强度 ──
    password_min_length: int = 8

    # ── bootstrap 默认账号 ──
    default_admin_password: str = "admin123456"

    # ── 数据源凭证加密 ──
    # Fernet 密钥 (urlsafe-base64, 32 字节)。生产必须用 IS_DATASOURCE_ENCRYPTION_KEY
    # 覆盖此 dev 默认值; 轮换密钥需先用旧 key 解密存量再用新 key 重写。
    datasource_encryption_key: str = "pzI3RfOdLDQzT1MK9q2irOGvvJ2XlUo6aIoChL09B0I="

    # ── agentic extractor 配置 (IS_AGENTIC_EXTRACT_*) ──
    agentic_extract_max_depth: int = 4  # noqa: hardcode
    """emit 收口嵌套深度上限 (IS_AGENTIC_EXTRACT_MAX_DEPTH)."""
    agentic_extract_max_iterations: int = 100  # noqa: hardcode
    """extraction agent 最大轮数 (IS_AGENTIC_EXTRACT_MAX_ITERATIONS).

    两阶段模型: 探索期 ~2000-3000 token/轮, 发射期 ~500-1500 token/轮.
    content-manage-service 实测 50 轮被截断 (发射中段, knowledge 全丢), 100 覆盖大部分 repo.
    极端大型仓库的单会话上下文溢出风险 → docs/todos/2026-06-19-agent-loop-context-ceiling.md.
    """
    agentic_extract_dead_loop_window: int = 5  # noqa: hardcode
    """dead_loop 检测窗口大小 (IS_AGENTIC_EXTRACT_DEAD_LOOP_WINDOW)."""
    agentic_extract_max_grep_pattern_len: int = 200  # noqa: hardcode
    """ReDoS 守卫 — grep 正则长度上限 (IS_AGENTIC_EXTRACT_MAX_GREP_PATTERN_LEN)."""
    agentic_enum_scan_timeout: int = 30  # noqa: hardcode
    """大仓 enum glob 超时秒数 (IS_AGENTIC_ENUM_SCAN_TIMEOUT)."""

    # ── Agentic Extractor — Skeleton (multi-session divide & conquer) ──
    agentic_extract_max_work_unit_size: int = 30  # noqa: hardcode
    """子代理单次最大实体数 (IS_AGENTIC_EXTRACT_MAX_WORK_UNIT_SIZE)."""
    agentic_extract_subagent_concurrency: int = 4  # noqa: hardcode
    """并行子代理最大并发 (IS_AGENTIC_EXTRACT_SUBAGENT_CONCURRENCY)."""

    @model_validator(mode="after")
    def _validate_error_class_invariants(self) -> "Settings":
        """启动期强制 Error_Class → Forced_Clarify 的两条数值不变量。

        1. threshold <= dead_loop_window (先手不变量): 否则 dead_loop 在更早迭代抢先终止,
           Forced_Clarify 永不触达。
        2. threshold <= window_size (可达性不变量): ErrorClassWindow.count(c) 上界即 window_size,
           threshold 超过它则计数永远到不了阈值, Forced_Clarify 被静默禁用。
        """
        if self.agent_loop_error_class_threshold > self.agent_loop_dead_loop_window:
            raise ValueError(
                "IS_AGENT_LOOP_ERROR_CLASS_THRESHOLD "
                f"({self.agent_loop_error_class_threshold}) 必须 <= "
                f"IS_AGENT_LOOP_DEAD_LOOP_WINDOW ({self.agent_loop_dead_loop_window}); "
                "否则 dead_loop 会在 Forced_Clarify 之前抢先终止。"
            )
        if self.agent_loop_error_class_threshold > self.agent_loop_error_class_window_size:
            raise ValueError(
                "IS_AGENT_LOOP_ERROR_CLASS_THRESHOLD "
                f"({self.agent_loop_error_class_threshold}) 必须 <= "
                "IS_AGENT_LOOP_ERROR_CLASS_WINDOW_SIZE "
                f"({self.agent_loop_error_class_window_size}); "
                "否则窗口容量不足以累积到阈值, Forced_Clarify 永不触发。"
            )
        return self

    @model_validator(mode="after")
    def _validate_thinking_budget(self) -> "Settings":
        if self.llm_claude_thinking_budget_tokens < 1024:
            raise ValueError(
                f"IS_LLM_CLAUDE_THINKING_BUDGET_TOKENS ({self.llm_claude_thinking_budget_tokens}) "
                "必须 >= 1024（Anthropic 最小值）"  # noqa: hardcode
            )
        return self


settings = Settings()
