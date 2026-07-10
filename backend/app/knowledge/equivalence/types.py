"""Equivalence checker 类型定义."""

from __future__ import annotations

from typing import TYPE_CHECKING, Awaitable, Literal, Protocol

if TYPE_CHECKING:
    from app.models import SchemaCanonicalCandidate

DbType = Literal["mysql", "mongodb", "postgresql", "*"]
CandidateKind = Literal[
    "table_description", "field_description",
    "enum_values", "relationship", "sample_values", "*"
]

CheckerResult = tuple["SchemaCanonicalCandidate", str] | None
"""checker 的统一返回类型: (winner, reason) 表示等价合并成功; None 表示不适用."""


class EquivalenceChecker(Protocol):
    """等价比较器 Protocol.

    每个 checker 是纯函数 (或 async 函数):
    - 输入: 同一 (db_type, database, target, field_path, kind) 的 N≥2 候选列表
    - 输出: (winner, reason) 表示等价合并成功; None 表示本 checker 不适用

    sync 与 async checker 共存 — promote 主流程统一以 `await` 调用,
    sync 函数返回的非 coroutine 结果由 `inspect.isawaitable` 判定后直接消费.
    类型注解上以 `CheckerResult | Awaitable[CheckerResult]` 联合表达.

    namespace_id 由 _try_rule 统一传入; 结构性 checker (不调 LLM) 可忽略该参数,
    LLM checker (如 semantic_llm) 用于按 namespace 解析模型配置.
    """

    def __call__(
        self,
        cands: list[SchemaCanonicalCandidate],
        *,
        namespace_id: int | None = None,
    ) -> CheckerResult | Awaitable[CheckerResult]:
        """返 (winner, reason) / None / 上述两者的 Awaitable."""
        ...
