"""knowledge_entries.payload 按 entry_type 的 Pydantic schemas.

每个 entry_type 有自己的 payload 结构, 由 parse_payload 分发校验.
不通过校验的 payload 写 audit_log warning 但不阻断主流程.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from app.config import settings


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
        max_len = settings.terminology_term_max_len
        out: list[str] = []
        for idx, s in enumerate(v):
            s_strip = s.strip() if s else ""
            if not s_strip:
                raise ValueError(f"synonym 不能为空白: 索引 {idx}")
            if len(s_strip) > max_len:
                raise ValueError(f"synonym 超长: {s_strip!r}")
            if "\n" in s_strip or "。" in s_strip:
                raise ValueError(f"synonym 不应含换行/句号: {s_strip!r}")
            out.append(s_strip)
        return out


class ExamplePayload(BaseModel):
    """统一 example payload — agent_learn + code_extract + trace_refiner 共用.

    question_pattern: 语义骨架, ChromaDB 索引入口.
    collections:      有序 db.collection 链 ["shop.orders", "shop.users"].
    join_keys:        跨表连接键 [{"from": "orders.user_id", "to": "users.id"}].
    final_query_plan: 统一查询计划 (db_type 多态内化在 step.query 中).
    result_summary:   自然语言描述 filter+join+aggregate 模式.
    """
    model_config = ConfigDict(extra="allow")

    question_pattern: str
    collections: list[str] = []
    join_keys: list[dict] = []
    final_query_plan: dict | None = None
    result_summary: str = ""

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
    applies_to_collections: list[str] = []
    priority: int = 0
    # Phase 2 P2.T13: 规则分类 + 证据 — 向后兼容
    rule_kind: Literal["business_constraint", "filter_default", "join_pattern"] = "business_constraint"
    evidence: dict | None = None


class RouteHintPayload(BaseModel):
    """决策路径偏好. agent loop 学到的多层关联策略."""
    model_config = ConfigDict(extra="forbid")

    question_pattern: str
    collection_path: list[str]
    join_fields: list[dict] = []
    avoid_path: list[str] = []
    cost_strategy: str = "default"
    reason: str = ""

    @field_validator("collection_path")
    @classmethod
    def _no_duplicate(cls, v: list[str]) -> list[str]:
        if len(v) != len(set(v)):
            raise ValueError("collection_path 不允许重复 collection 名")
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
