"""knowledge_entries.payload 按 entry_type 的 Pydantic schemas.

每个 entry_type 有自己的 payload 结构, 由 parse_payload 分发校验.
不通过校验的 payload 写 audit_log warning 但不阻断主流程.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from app.config import settings


class CollectionRef(BaseModel):
    """带 database 的集合引用 — 多源关联下表/集合名不唯一, 必须配 database 定位.

    存储富形态: KE.payload 里集合字段统一用此结构. 输出侧(召回给 LLM)投影成
    "database.collection" 字符串; HQ 路径校验取裸 collection 名(拓扑无关 database).
    database 与 collection 均不得含 "." (输出投影用点分, 含 "." 致回解歧义).
    """
    model_config = ConfigDict(extra="forbid")

    database: str
    collection: str


class TerminologyPayload(BaseModel):
    """术语映射 — Phase 1 升级: 必填字段收紧 + term shape 校验."""

    model_config = ConfigDict(extra="forbid")

    term: str
    primary_collection: str
    primary_database: str
    db_type: Literal["mysql", "mongodb", "oracle"]  # ← 与 DRIVERS 注册表同步 (SUPPORTED_DB_TYPES); Python 不支持 Literal[*frozenset] 故手动列举
    primary_field: str | None = None
    synonyms: list[str] = []
    source_collections: list[str] = []

    @field_validator("term")
    @classmethod
    def _term_shape_check(cls, v: str) -> str:
        v_stripped = v.strip() if v else ""
        if not v_stripped:
            raise ValueError("term 不能为空")
        max_len = settings.terminology_term_max_len
        if len(v_stripped) > max_len:
            raise ValueError(
                f"term 应为单一业务名词 (≤{max_len} 字), 当前 {len(v_stripped)} 字, "
                f"多段描述请拆分为多个原子条目"
            )
        if "\n" in v_stripped:
            raise ValueError("term 不应含换行")
        if "。" in v_stripped or "；" in v_stripped or ";" in v_stripped:
            raise ValueError("term 不应含句号/分号, 多句内容请改 rule 类型")
        return v_stripped

    @field_validator("synonyms")
    @classmethod
    def _synonyms_shape_check(cls, v: list[str]) -> list[str]:
        import logging
        _log = logging.getLogger(__name__)
        # synonym 用独立上限（释义性短语比 term 长，英文术语天然超 20 字）
        max_len = settings.terminology_synonym_max_len
        out: list[str] = []
        for s in v:
            s_strip = s.strip() if s else ""
            if not s_strip:
                continue  # 空白项静默跳过
            if len(s_strip) > max_len:
                _log.warning("[terminology] synonym 超长，已跳过: %r (len=%d > %d)",
                             s_strip, len(s_strip), max_len)
                continue  # 单个超长项 drop，不连累整条 term
            if "\n" in s_strip or "。" in s_strip:
                _log.warning("[terminology] synonym 含非法字符，已跳过: %r", s_strip)
                continue  # 同上
            out.append(s_strip)
        return out


class ExamplePayload(BaseModel):
    """统一 example payload — agent_learn + code_extract + trace_refiner 共用.

    question_pattern: 语义骨架, ChromaDB 索引入口.
    collections:      有序集合链 [{database, collection}].
    join_keys:        跨表连接键 [{"from": "orders.user_id", "to": "users.id"}].
    final_query_plan: 统一查询计划 (db_type 多态内化在 step.query 中).
    result_summary:   自然语言描述 filter+join+aggregate 模式.
    """
    model_config = ConfigDict(extra="allow")

    question_pattern: str
    collections: list[CollectionRef] = []
    join_keys: list[dict] = []
    final_query_plan: dict | None = None
    result_summary: str = ""

    # Phase 2 P2.T13: NL paraphrases 索引升级 — 向后兼容
    nl_paraphrases: list[str] = []
    dynamic_variants: list[dict] = []
    extraction_source: Literal["qmql_history", "mybatis_extract"] = "qmql_history"
    source_mapper: str | None = None
    source_method: str | None = None
    source_repo_id: int | None = None
    explain_verified: bool = False

    @field_validator("result_summary")
    @classmethod
    def _result_summary_len_check(cls, v: str) -> str:
        max_len = settings.example_result_summary_max_len
        if len(v) > max_len:
            raise ValueError(
                f"result_summary 超过字数上限 {max_len}（当前 {len(v)} 字）"
            )
        return v


class RulePayload(BaseModel):
    """查询规则. 替代 namespace_rules."""
    model_config = ConfigDict(extra="forbid")

    rule_text: str
    applies_to_collections: list[CollectionRef] = []
    priority: int = 0
    # Phase 2 P2.T13: 规则分类 + 证据 — 向后兼容
    rule_kind: Literal["business_constraint", "filter_default", "join_pattern"] = "business_constraint"
    evidence: dict | None = None


class RouteHintPayload(BaseModel):
    """跨集合导航知识 — deliberate 录入 (手动 API / agent save_knowledge), 无自动抽取.

    spec 2026-07-08. question_pattern 不在 payload — 它是向量检索键,
    唯一真相源是 KnowledgeEntry.content.
    collection_path: 有序集合路径, 每段带 database (多源下集合名不唯一).
    navigation_note 是人 deliberate 写的完整字段路径散文 (关联字段/关联类型/嵌套位置/避坑).
    """
    model_config = ConfigDict(extra="forbid")

    collection_path: list[CollectionRef] = []
    navigation_note: str = ""

    @field_validator("collection_path")
    @classmethod
    def _no_duplicate(cls, v: list[CollectionRef]) -> list[CollectionRef]:
        seen = {(r.database, r.collection) for r in v}
        if len(v) != len(seen):
            raise ValueError("collection_path 不允许重复 (database, collection) 组合")
        return v


_PAYLOAD_REGISTRY: dict[str, type[BaseModel]] = {
    "terminology": TerminologyPayload,
    "example": ExamplePayload,
    "rule": RulePayload,
    "route_hint": RouteHintPayload,
}


def parse_payload(entry_type: str, raw: dict) -> BaseModel:
    """按 entry_type 分发到对应 Pydantic schema 校验."""
    cls = _PAYLOAD_REGISTRY.get(entry_type)
    if cls is None:
        raise ValueError(f"unknown entry_type: {entry_type!r}")
    return cls(**raw)
