"""
Pydantic 请求/响应模型
数据在边界验证, 内部只流转可信对象
"""

from __future__ import annotations

import json
import re as _re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.config import settings as _settings
from app.models.knowledge_entry import KnowledgeStatus


def validate_password_strength(v: str) -> str:
    """密码 >= 配置最小长度, 且字母+数字混合。"""
    if len(v) < _settings.password_min_length:
        raise ValueError(f"密码至少 {_settings.password_min_length} 位")
    if not (_re.search(r"[A-Za-z]", v) and _re.search(r"\d", v)):
        raise ValueError("密码必须包含字母和数字")
    return v


# ════════════════════════════════════════════
#  命名空间
# ════════════════════════════════════════════

class NamespaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)  # noqa: hardcode
    slug: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9_-]+$")  # noqa: hardcode
    description: str = ""
    git_token: str = ""


class NamespaceUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    git_token: str | None = None


class NamespaceOut(BaseModel):
    id: int
    name: str
    slug: str
    description: str
    git_token_masked: str = ""
    created_at: datetime
    created_by: int | None = None

    model_config = {"from_attributes": True}


class NamespaceDeletePreview(BaseModel):
    """DELETE /api/namespaces/{ns_id}?dry_run=true 预览报告 (Stage 2 Task 4).

    桥接 BulkOpReport → HTTP — 客户端据此弹出"确认 X 条 KE 将被删"对话框,
    并把 confirm_token 原样回填到 dry_run=false 调用. confirm_required 由
    settings.bulk_op_require_confirm_above 阈值决定, False 时 token 留空.
    """

    op_name: str
    affected_count: int
    by_source: dict[str, int]
    by_entry_type: dict[str, int]
    preserved_audited_count: int
    sample_ids: list[int]
    confirm_required: bool
    confirm_token: str | None = None


# ════════════════════════════════════════════
#  数据源
# ════════════════════════════════════════════

class DataSourceCreate(BaseModel):
    db_type: str = Field(pattern=r"^(mysql|mongodb|oracle)$")  # ← 与 DRIVERS 注册表同步, 见 engine/db_types.py:SUPPORTED_DB_TYPES
    host: str
    port: int

    @field_validator("db_type")
    @classmethod
    def _db_type_is_supported(cls, v: str) -> str:
        from app.engine.db_types import SUPPORTED_DB_TYPES
        if v not in SUPPORTED_DB_TYPES:
            raise ValueError(f"不支持的 db_type: {v!r}, 仅支持: {sorted(SUPPORTED_DB_TYPES)}")
        return v
    database: str
    username: str
    password: str  # 明文传入, 服务端加密存储
    description: str = ""  # 用户填写: 这个库是干嘛的, 给 LLM 看
    timezone: str

    @field_validator("timezone")
    @classmethod
    def _timezone_is_valid_iana(cls, v: str) -> str:
        from zoneinfo import available_timezones
        if v not in available_timezones():
            raise ValueError(f"非法 IANA 时区名: {v!r}")
        return v


class DataSourceOut(BaseModel):
    id: int
    db_type: str
    host: str
    port: int
    database: str
    username: str
    description: str
    db_profile: dict
    timezone: str
    created_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_ds(cls, ds) -> "DataSourceOut":
        """从 DataSource ORM 构造, 把 db_profile_json 字符串解析为 dict."""
        try:
            profile = json.loads(ds.db_profile_json or "{}")
        except (json.JSONDecodeError, TypeError):
            profile = {}
        return cls(
            id=ds.id, db_type=ds.db_type, host=ds.host, port=ds.port,
            database=ds.database, username=ds.username,
            description=ds.description, db_profile=profile,
            timezone=ds.timezone, created_at=ds.created_at,
        )


class DataSourceProbeIn(BaseModel):
    """probe 端点入参: 连通+时区探测, 不含 timezone (detected_timezone 是探测输出)."""
    db_type: str

    @field_validator("db_type")
    @classmethod
    def _db_type_is_supported(cls, v: str) -> str:
        from app.engine.db_types import SUPPORTED_DB_TYPES
        if v not in SUPPORTED_DB_TYPES:
            raise ValueError(f"不支持的 db_type: {v!r}, 仅支持: {sorted(SUPPORTED_DB_TYPES)}")
        return v

    host: str
    port: int
    database: str
    username: str
    password: str
    description: str = ""


class DataSourceProbeOut(BaseModel):
    connected: bool
    detected_timezone: str | None = None
    failure_reason: str | None = None


class SchemaRefreshResult(BaseModel):
    success: bool
    table_count: int = 0
    message: str = ""


# ════════════════════════════════════════════
#  Git 仓库
# ════════════════════════════════════════════

class GitRepoCreate(BaseModel):
    url: str
    branch: str = "master"
    profile_id: int | None = None    # agentic extractor profile
    git_token: str = ""


class GitRepoOut(BaseModel):
    id: int
    url: str
    branch: str
    parse_status: str
    error_message: str
    created_at: datetime
    parsed_at: datetime | None
    has_report: bool = False
    completeness_score: int = 0
    worker_id: str = ""
    progress: int = 0
    progress_message: str = ""
    profile_id: int | None = None    # agentic extractor profile (NULL=自动识别)
    git_token_masked: str = ""
    model_config = {"from_attributes": True}


class BatchStatus(BaseModel):
    """二轮自答进度 — 通过 repos 轮询 piggyback 返回"""
    active: bool = False
    progress: str = ""  # e.g. "3/7 repos"
    message: str = ""


class RepoListResponse(BaseModel):
    repos: list[GitRepoOut]
    batch_status: BatchStatus | None = None


# ════════════════════════════════════════════
#  仓库 ↔ 数据源映射
# ════════════════════════════════════════════

class RepoMappingCreate(BaseModel):
    datasource_id: int


class RepoMappingOut(BaseModel):
    id: int
    repo_id: int
    datasource_id: int

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════
#  知识条目
# ════════════════════════════════════════════

class KnowledgeEntryCreate(BaseModel):
    # Stage 1 写入治理: entry_type 5 类宪章 (与 app/knowledge/intake.VALID_ENTRY_TYPES 同源)
    entry_type: str = Field(
        pattern=r"^(terminology|instance_alias|example|rule|route_hint)$"
    )
    content: str = Field(min_length=1)
    namespace_id: int | None = None  # None = 全局
    tier: str = Field(default="normal", pattern=r"^(critical|normal)$")
    # Phase 1c Task 1.5 — terminology 通道走统一闸门时必传:
    #   payload     — TerminologyPayload dict (term/primary_collection/primary_database/db_type/...)
    #   raw_input   — 用户原始输入 (闸门 _create_proposed 写入 KnowledgeEntry.raw_input)
    #   evidence    — 来源证据 (闸门写入 KnowledgeEntry.evidence_json)
    # 其他 4 类 entry_type 不读这些字段, 走 refine→conflict→KE 既有路径.
    payload: dict[str, Any] | None = None
    raw_input: str = ""
    evidence: dict[str, Any] | None = None


class KnowledgeEntryOut(BaseModel):
    id: int
    namespace_id: int | None
    entry_type: str
    tier: str
    content: str
    raw_input: str
    description: str
    source: str
    is_superseded: bool
    status: str = "proposed"  # Stage 1: proposed|canonical|superseded|rejected
    payload: dict[str, Any] | None = None
    hypothetical_queries_json: str = "[]"  # Stage 2 Task A
    related_entry_ids_json: str = "[]"     # Stage 2 Task D
    refined_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}

    # ── DB 里 payload 是 JSON 字符串, 序列化前解析为 dict 让前端直接消费 ──
    @field_validator("payload", mode="before")
    @classmethod
    def _parse_payload_json(cls, v: Any) -> Any:
        if isinstance(v, str):
            try:
                return json.loads(v) if v else None
            except json.JSONDecodeError:
                return None
        return v


class KnowledgeEntryUpdate(BaseModel):
    """PATCH /api/knowledge/{id} — 所有字段可选, 只更新传入字段."""
    content: str | None = Field(default=None, min_length=1)
    tier: str | None = Field(default=None, pattern=r"^(critical|normal)$")
    description: str | None = None
    status: KnowledgeStatus | None = Field(default=None)


class ConflictItemOut(BaseModel):
    existing_id: int
    reason: str
    suggested: str  # merge | replace | coexist


class KnowledgeEntryDraft(BaseModel):
    refined: str
    description: str


class KnowledgeEntryCreateResponse(BaseModel):
    entry: KnowledgeEntryOut | None = None
    conflicts: list[ConflictItemOut] = []
    overflow: bool = False
    split_candidates: list[KnowledgeEntryDraft] = []


# ════════════════════════════════════════════
#  Stage 3 审核队列
# ════════════════════════════════════════════

class AuditQueueOut(BaseModel):
    """Stage 3 GET /api/knowledge/audit/queue 响应 — 分页待审/已审条目列表."""
    items: list[KnowledgeEntryOut]
    total: int
    page: int
    size: int

    model_config = {"from_attributes": True}


class AuditApproveBody(BaseModel):
    """POST /audit/{id}/approve body — 审核通过 + 可选编辑 + 可选 supersede 旧条目.

    edits 可选三字段 {content, payload, tier}; supersede_ids 中的旧 canonical 将转
    superseded, superseded_by=entry_id, 各写一条 audit_log(action=supersede).
    """
    edits: dict[str, Any] | None = None
    supersede_ids: list[int] = []
    reason: str = ""

    model_config = {"extra": "forbid"}


class AuditRejectBody(BaseModel):
    """POST /audit/{id}/reject body — reason 必填, 审核员拒绝原因."""
    reason: str = Field(min_length=1)

    model_config = {"extra": "forbid"}


# ── Stage 3 Task 3: 批量审核 ──

class AuditBatchAction(BaseModel):
    """单条批量动作 — action 必须 ∈ {approve, reject}."""
    entry_id: int
    action: str = Field(pattern=r"^(approve|reject)$")
    reason: str = ""
    edits: dict[str, Any] | None = None  # approve 用
    supersede_ids: list[int] = []  # approve 用

    model_config = {"extra": "forbid"}


class AuditBatchBody(BaseModel):
    """POST /audit/batch body — actions 列表 + 可选 confirm_token (超阈值时强制)."""
    actions: list[AuditBatchAction] = Field(min_length=1)
    confirm_token: str | None = None

    model_config = {"extra": "forbid"}


class AuditBatchOut(BaseModel):
    """POST /audit/batch 响应 — affected_count + success_ids 即完整契约.

    Why all-or-nothing: 不暴露 failed/audit_log_ids 字段 — 单事务模式下要么全成功
    (success_ids = 全部 entry_id) 要么全失败 (422 raised), 不存在中间态 partial result.
    审计 ID 通过 GET /audit/{entry_id}/log 按需查询 (Task 4).
    """
    affected_count: int
    success_ids: list[int]


# ── Stage 3 Task 4: restore + log ──

class AuditRestoreBody(BaseModel):
    """POST /api/knowledge/{id}/restore body — reason 必填.

    rejected → canonical 反向状态机, 与 reject 对称: 都要审核员留痕原因.
    """
    reason: str = Field(min_length=1)

    model_config = {"extra": "forbid"}


class AuditLogOut(BaseModel):
    """audit_log 单条记录响应 (按 created_at asc 列出, 供 timeline UI).

    diff_json 保留 raw JSON 字符串 — 前端按需 JSON.parse, 后端不强约束 schema
    (不同 action 的 diff 形态差异大: edit 装 before/after, supersede 空, bulk_* 含 op_id).
    """
    id: int
    entry_id: int | None
    actor_id: int | None
    action: str
    from_status: str | None
    to_status: str
    reason: str
    diff_json: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Stage 3 Task 5: PUT /api/knowledge/{id} 编辑端点 ──

class EditCanonicalBody(BaseModel):
    """PUT /api/knowledge/{id} body — Stage 3 升级版含 audit + payload 校验 + 冲突检测.

    reason 必填: 写入 audit_log.reason 供时间线追溯.
    payload 走 parse_payload(entry_type) 严格 Pydantic schema 校验, 失败 422.
    edits 仅接受 ALLOWED_EDIT_FIELDS = {content, payload, tier, hypothetical_queries} 字段.
    """
    content: str | None = Field(default=None, min_length=1)
    tier: str | None = Field(default=None, pattern=r"^(critical|normal)$")
    payload: dict[str, Any] | None = None
    hypothetical_queries: list[str] | None = None
    reason: str = Field(min_length=1)

    model_config = {"extra": "forbid"}


class EditCanonicalOut(BaseModel):
    """PUT /api/knowledge/{id} 响应 — entry + 编辑后冲突 (非阻塞)."""
    entry: KnowledgeEntryOut
    conflicts: list[ConflictItemOut] = []


# ── Stage 3 Task 7: POST /audit/conflict-preview 实时冲突预览 ──

class ConflictPreviewBody(BaseModel):
    """POST /audit/conflict-preview body — 编辑表单实时冲突检测 (debounce 500ms 调一次).

    只读 — 不写库, 不动 ChromaDB, 仅借 LLM 比对同 namespace + 同 entry_type 的 canonical.
    entry_id 给定时排除自身 (编辑场景防自比), 否则按"新建"语义全量比对.
    """
    namespace_id: int | None = None
    entry_type: str = Field(
        pattern=r"^(terminology|instance_alias|example|rule|route_hint)$"
    )
    content: str = Field(min_length=1)
    entry_id: int | None = None  # 编辑场景排除自身

    model_config = {"extra": "forbid"}


class ConflictPreviewOut(BaseModel):
    """conflict-preview 响应 — 与 KnowledgeEntryCreateResponse.conflicts 同 schema."""
    conflicts: list[ConflictItemOut] = []


# ════════════════════════════════════════════
#  查询
# ════════════════════════════════════════════

class QueryRequest(BaseModel):
    namespace_id: int
    question: str = Field(min_length=1)
    session_id: str | None = None  # 多轮对话 session, 首次可不传


class QueryResponse(BaseModel):
    session_id: str = ""
    history_id: int = 0  # 成功查询的历史记录 ID, 用于分享功能
    needs_clarification: bool = False
    clarification_message: str = ""
    generated_query: str = ""
    columns: list[str] = []
    rows: list[list[Any] | dict[str, Any]] = []
    row_count: int = 0
    chart_type: str = "table"  # line | bar | pie | card | table
    chart_option: dict[str, Any] = {}
    performance_warning: str = ""  # 查询性能提示 (非阻断, 仅告知)
    # §4.6 截断显式 (绝不静默): 渲染源撞 IS_RENDER_ROW_LIMIT 时透传
    truncated: bool = False
    rendered_row_count: int = 0
    total_row_count: int = 0
    error: str = ""
    # Decomposer Routing P1 — 结构化澄清 (ClarifyQuestionCard UI)
    clarification_questions: list["ClarifyQuestion"] = []
    pending_id: int = 0  # 0 表示无 pending


class ClarifyOption(BaseModel):
    """单条候选选项 — external_entity 预查命中的一个实体."""
    value: str                  # entity _id (str 化, 兼容 ObjectId)
    label: str                  # 展示名 (如 "优选系列·家居·上架")
    meta: dict[str, Any] = {}   # 附加元数据 (version/status/level 等)


class ClarifyQuestion(BaseModel):
    """单条结构化澄清问题 — 针对一个 condition 的候选列表."""
    cond_id: str                # 稳定标识, 回传时识别 condition
    prompt: str                 # "请选择具体的商品"
    options: list[ClarifyOption] = []
    mode: str = "single"        # single (radio) | multi (checkbox)
    overflow: bool = False      # 候选 > 阈值时为 True, 前端显示"缩小范围"
    sample_count: int = 0       # overflow 时的样本数 (candidates 只取前 N)
    empty: bool = False         # True = 0 候选, 前端显示"未找到"


class QueryContinueRequest(BaseModel):
    """POST /api/query/continue — 用户勾选后续跑."""
    pending_id: int
    # cond_id → 选中的 value 列表 (single mode 长度 1, multi mode 可多个)
    selections: dict[str, list[str]] = {}


class PendingClarificationOut(BaseModel):
    """GET /api/query/pending/{id} — pending 详情 (前端重新拉选项用)."""
    id: int
    session_id: str
    namespace_id: int
    original_question: str
    status: str                 # pending | resolved | abandoned | overflow
    clarification_questions: list[ClarifyQuestion] = []
    created_at: datetime
    expires_at: datetime

    model_config = {"from_attributes": True}


class QueryExplainResponse(BaseModel):
    generated_query: str
    explanation: str


# ════════════════════════════════════════════
#  查询历史
# ════════════════════════════════════════════

class QueryHistoryOut(BaseModel):
    id: int
    session_id: str
    role: str
    content: str
    generated_query: str
    row_count: int
    error: str
    result_snapshot: str = ""
    created_at: datetime

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════
#  解析报告 — Git 仓库解析质量评估
# ════════════════════════════════════════════

class ParserStatsOut(BaseModel):
    files_scanned: int = 0
    files_parsed: int = 0
    files_skipped: int = 0
    files_errored: int = 0
    items_extracted: int = 0
    tables_found: list[str] = []


class ParseReportOut(BaseModel):
    repo_id: int
    duration_seconds: float = 0.0
    stats: ParserStatsOut = ParserStatsOut()
    ddls_trained: int = 0
    docs_trained: int = 0
    sqls_trained: int = 0
    completeness_score: int = 0
    evaluation_summary: str = ""


# ════════════════════════════════════════════
#  认证
# ════════════════════════════════════════════

class LoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    access_token: str
    user: "UserOut"


class PasswordChangeRequest(BaseModel):
    old_password: str = Field(min_length=1)
    new_password: str

    _v_pwd = field_validator("new_password")(validate_password_strength)


class PasswordResetRequest(BaseModel):
    new_password: str

    _v_pwd = field_validator("new_password")(validate_password_strength)


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str
    role: str = Field(pattern=r"^(super_admin|admin|user)$", default="user")

    _v_pwd = field_validator("password")(validate_password_strength)


class UserUpdate(BaseModel):
    role: str | None = Field(pattern=r"^(super_admin|admin|user)$", default=None)
    is_active: bool | None = None


class UserOut(BaseModel):
    id: int
    username: str
    role: str
    is_active: bool
    created_at: datetime
    created_by: int | None = None
    model_config = {"from_attributes": True}


class UserAccessUpdate(BaseModel):
    namespace_ids: list[int]


# ════════════════════════════════════════════
#  分享
# ════════════════════════════════════════════

class ShareCreate(BaseModel):
    query_history_id: int
    expires_at: datetime | None = None


class ShareOut(BaseModel):
    id: int
    token: str
    query_history_id: int
    shared_by: int
    created_at: datetime
    expires_at: datetime | None
    is_active: bool

    model_config = {"from_attributes": True}


class ShareViewOut(BaseModel):
    shared_at: datetime
    shared_by_name: str
    result: QueryResponse


# ════════════════════════════════════════════
#  Mongo Canonical Knowledge
# ════════════════════════════════════════════

# MongoCanonicalCollectionOut 已移除 (Phase 5 T4 mongo-canonical-retirement).
# MongoKnowledgeConflictOut / MongoConflictResolveRequest 已移除 —
# Mongo 字段类型冲突统一走 SchemaCanonicalConflict 端点 (Phase 4 完成迁移).


# ════════════════════════════════════════════
#  Stage 5 — SSE 流式查询
# ════════════════════════════════════════════
from app.schemas.query_stream import (  # noqa: E402
    ClarifyResponseRequest,
    CorrectionRequest,
    QueryStreamRequest,
)


# ════════════════════════════════════════════
#  P0 对话体验 — Feedback
# ════════════════════════════════════════════

class FeedbackCreate(BaseModel):
    history_id: int
    rating: Literal["like", "dislike"]


# ════════════════════════════════════════════
#  P0 对话体验 — Readiness
# ════════════════════════════════════════════

class BlockerOut(BaseModel):
    type: str  # no_datasource | no_api_key | no_schema | no_access
    message: str
    admin_action: str
    admin_route: str = ""
    user_action: str


class ReadinessOut(BaseModel):
    ready: bool
    checks: dict[str, bool]
    blockers: list[BlockerOut]


# ════════════════════════════════════════════
#  P0 对话体验 — Session
# ════════════════════════════════════════════

class SessionCreate(BaseModel):
    namespace_id: int


class SessionUpdate(BaseModel):
    title: str = Field(min_length=1)


class SessionOut(BaseModel):
    id: UUID  # Pydantic auto-serializes to str in JSON
    namespace_id: int
    title: str
    created_by: int
    created_at: datetime
    updated_at: datetime | None

    model_config = {"from_attributes": True}


# ════════════════════════════════════════════
#  工作台首页汇总
# ════════════════════════════════════════════

class WorkbenchNamespaceCardOut(BaseModel):
    id: int
    name: str
    slug: str
    description: str
    created_at: datetime
    ready: bool
    datasource_count: int
    session_count: int
    git_parsed_count: int
    git_total_count: int
    knowledge_count: int


class WorkbenchRecentSessionOut(BaseModel):
    id: str
    namespace_id: int
    namespace_name: str
    title: str
    updated_at: datetime | None


class WorkbenchSummaryOut(BaseModel):
    accessible_count: int
    ready_count: int
    pending_count: int
    recent_session_count: int
    namespaces: list[WorkbenchNamespaceCardOut]
    recent_sessions: list[WorkbenchRecentSessionOut]


# ════════════════════════════════════════════
#  查询（最后一个占位符，保留模块空白尾部）
# ════════════════════════════════════════════
