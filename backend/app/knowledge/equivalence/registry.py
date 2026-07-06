"""Equivalence rule registry — 全局注册表 + 查询."""

from __future__ import annotations

from dataclasses import dataclass

from app.knowledge.equivalence.types import (
    CandidateKind,
    DbType,
    EquivalenceChecker,
)


@dataclass(frozen=True)
class EquivalenceRule:
    """一条等价比较规则.

    Attributes:
        db_type: 匹配的数据库类型, "*" 表示任意
        kind: 匹配的 candidate_kind, "*" 表示任意
        priority: 越小越先尝试 (0 最高)
        checker: 符合 EquivalenceChecker Protocol 的可调用对象 (sync 或 async)
        name: 日志/审计用标识
    """

    db_type: DbType
    kind: CandidateKind
    priority: int
    checker: EquivalenceChecker
    name: str


_REGISTRY: list[EquivalenceRule] = []


def register(rule: EquivalenceRule) -> None:
    """注册一条等价规则, 自动按 priority 排序."""
    _REGISTRY.append(rule)
    _REGISTRY.sort(key=lambda r: r.priority)


def applicable_rules(db_type: str, kind: str) -> list[EquivalenceRule]:
    """返回匹配 (db_type, kind) 的规则列表, 已按 priority 升序排好.

    匹配逻辑:
    - rule.db_type == db_type OR rule.db_type == "*"
    - rule.kind == kind OR rule.kind == "*"
    - kind=="relationship" → 多源分歧不走语义等价 (semantic_llm 吞 conflict)
    """
    if kind == "relationship":
        return []
    return [
        r for r in _REGISTRY
        if (r.db_type == db_type or r.db_type == "*")
        and (r.kind == kind or r.kind == "*")
    ]


def _clear_for_tests() -> None:
    """测试专用: 清空注册表. 不要在生产代码中调用."""
    _REGISTRY.clear()
