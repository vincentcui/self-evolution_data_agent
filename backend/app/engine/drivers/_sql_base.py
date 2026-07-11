"""SQL 驱动共享基类 — 三态 cap + 截断判定 + count_wrap + execute_query 主流程.

方言子类 (MySQL/Oracle/未来 PG) 实现抽象钩子, 共享逻辑统一在此, 杜绝 _wrap_by_mode
散落 (count-wrap double-COUNT bug 的结构根因). 加 SQL 数据库只继承 + 覆写钩子.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod

from app.config import settings
from app.engine.drivers._exceptions import PayloadShapeMismatchError
from app.engine.drivers.base import ExecuteResult


class BaseSqlDriver(ABC):
    """SQL 驱动共享基类.

    方言钩子 (抽象): _normalize / _has_outer_limit / _outer_limit_value /
    _strip_outer_limit_impl / _inject_limit / _enforce_select_only / _exec.
    共享: strip_outer_row_limit / count_wrap / _apply_cap / execute_query.
    """

    db_type: str
    paradigm: str = "relational"

    # ── 方言钩子 (子类实现) ──────────────────────────────
    @staticmethod
    @abstractmethod
    def _normalize(sql: str) -> str: ...

    @staticmethod
    @abstractmethod
    def _has_outer_limit(sql: str) -> bool: ...

    @staticmethod
    @abstractmethod
    def _outer_limit_value(sql: str) -> int | None: ...

    @staticmethod
    @abstractmethod
    def _strip_outer_limit_impl(sql: str) -> str: ...

    @staticmethod
    @abstractmethod
    def _inject_limit(sql: str, n: int) -> str: ...

    @staticmethod
    @abstractmethod
    def _enforce_select_only(sql: str) -> None: ...

    @abstractmethod
    async def _exec(self, ds, sql: str) -> list[dict]: ...

    # ── 共享逻辑 ─────────────────────────────────────────
    def strip_outer_row_limit(self, sql: str) -> str:
        """剥离最外层行保护 (供 plan_executor 补 count)."""
        return self._strip_outer_limit_impl(sql)

    def count_wrap(self, sql: str) -> str:
        """系统补 count: 包 COUNT 返标量. 跨方言统一 (alias _sub 不带 AS, 三家都认)."""
        return f"SELECT COUNT(*) AS cnt FROM ({sql}) _sub"

    def _apply_cap(self, sql: str) -> tuple[str, int]:
        """2-number 三态: 返 (执行 sql, 生效 limit).

        无 LIMIT → 注入 default_limit; LIMIT > hard_ceiling → 剥离+注入 ceiling;
        LIMIT ≤ ceiling → 保留 LLM 原值 (尊重意图). 不包 COUNT (count 由 LLM 自写或
        plan_executor 调 count_wrap).
        """
        base = self._normalize(sql)
        if not self._has_outer_limit(base):
            return self._inject_limit(base, settings.default_limit), settings.default_limit
        val = self._outer_limit_value(base)
        if val is not None and val > settings.hard_ceiling:
            return (
                self._inject_limit(self._strip_outer_limit_impl(base), settings.hard_ceiling),
                settings.hard_ceiling,
            )
        return base, val if val is not None else settings.default_limit

    async def execute_query(self, ds, target, query: dict) -> ExecuteResult:
        """执行查询: 校验 → 三态 cap → 执行 → 截断判定. 无 mode 参数."""
        sql = query.get("sql")
        if not sql:
            raise PayloadShapeMismatchError(
                "execute_query 需要 query.sql",
                suggestion="payload 必须包含 'sql' key",
            )
        self._enforce_select_only(sql)
        sql, applied = self._apply_cap(sql)
        t0 = time.perf_counter()
        rows = await self._exec(ds, sql)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        truncated = len(rows) >= applied
        return ExecuteResult(
            rows=rows, row_count=len(rows), truncated=truncated, elapsed_ms=elapsed_ms,
        )
