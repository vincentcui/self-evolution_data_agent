"""
plan_models — 查询规划数据结构 (Stage 4 Task 12 抽出)

Decomposer 全栈删除后, plan_generator / plan_executor / tools/plan_tools
通过 agent loop 编排消费的 dataclass 集中此处.

数据结构来源:
- PlanStep / QueryPlan  ← 原 plan_generator.py

跨引擎 (Plan A): PlanStep.db_type 区分 mysql / mongodb, 由 plan_generator 按
collections[].db_type 落到每个 step, plan_executor 据此多态 dispatch.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ══════════════════════════════════════════════════════════════════════════════
#  共享常量
# ══════════════════════════════════════════════════════════════════════════════

# 能力限制无配置 equivalent_hint 时的通用兜底改写建议 (R2: 单一中性归属,
# 由 plan_generator 与 plan_executor 共同 import, 禁止两份拷贝).
GENERIC_RESTRICTION_HINT = "改用不触发该限制的等价写法 (例如拆成多步、把表达式上移到 $set、或在应用层处理)."


# ══════════════════════════════════════════════════════════════════════════════
#  Query Plan
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class PlanStep:
    """单步执行单元.

    db_type 区分引擎: "mysql" → query={"sql": "..."}; "mongodb" → pipeline/query.
    缺省 "mongodb" 仅为向后兼容历史 plan dict.
    """
    step_idx: int                                          # 1-based
    database: str
    collection: str                                        # MongoDB: 集合名; MySQL: 表名
    operation: str                                         # find/aggregate/count_documents/sql
    pipeline: list = field(default_factory=list)           # aggregate 用
    query: dict = field(default_factory=dict)              # find 用 / sql 用 {"sql": "..."}
    projection: dict = field(default_factory=dict)
    sort: list = field(default_factory=list)
    exports: list[str] = field(default_factory=list)
    db_type: str = "mongodb"                               # "mysql" | "mongodb"

    def to_dict(self) -> dict:
        return {
            "step_idx": self.step_idx,
            "db_type": self.db_type,
            "database": self.database,
            "collection": self.collection,
            "operation": self.operation,
            "pipeline": self.pipeline,
            "query": self.query,
            "projection": self.projection,
            "sort": self.sort,
            "exports": self.exports,
        }


@dataclass
class QueryPlan:
    strategy: str                 # single_aggregate | multi_step
    steps: list[PlanStep]
    post_process: str = ""
    raw_llm_output: str = ""

    def to_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "steps": [s.to_dict() for s in self.steps],
            "post_process": self.post_process,
        }

    @property
    def databases(self) -> list[str]:
        return sorted({s.database for s in self.steps if s.database})


__all__ = [
    "GENERIC_RESTRICTION_HINT",
    "PlanStep",
    "QueryPlan",
]
