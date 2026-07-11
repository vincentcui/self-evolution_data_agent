"""DataSourceDriver Protocol + 共享数据结构."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict, runtime_checkable

from app.models import DataSource


@dataclass
class ProbeResult:
    """驱动探测数据库连接的结果.

    connected:        是否成功连接
    detected_timezone:探测到的数据库时区 (IANA 名, 歧义/失败时为 None)
    failure_reason:   连接失败原因 (connected=False 时填, 否则 None)
    """
    connected: bool
    detected_timezone: str | None = None
    failure_reason: str | None = None


class FieldDef(TypedDict):
    name: str
    type: str
    description: str
    indexed: bool
    nullable: bool


class SchemaSnapshot(TypedDict):
    db_type: str
    database: str
    target: str
    description: str
    fields: list[FieldDef]
    indexes: list[dict]
    sample_count: int


class CostEstimate(TypedDict):
    estimated_rows: int
    warning_level: Literal["ok", "high", "blocked"]
    raw_explain: dict


class ExecuteResult(TypedDict):
    rows: list[dict]
    row_count: int
    truncated: bool
    elapsed_ms: int


@runtime_checkable
class SqlDataSourceDriver(Protocol):
    """SQL 型数据源驱动额外约定: 暴露行数保护剥离方法, 供 plan_executor render/count 路径使用.

    MySQL 与 Oracle driver 均需实现此方法; MongoDB driver 不实现.
    plan_executor 在 SQL 分支通过 get_driver(db_type).strip_outer_row_limit(sql) 调用,
    不 import 任何具体 driver 类.
    """

    def strip_outer_row_limit(self, sql: str) -> str:
        """剥离最外层行数保护 (MySQL LIMIT / Oracle ROWNUM wrapper), 供 executor render/count 用."""
        ...

    def count_wrap(self, sql: str) -> str:
        """系统补 count 用: 包 COUNT 返标量 (plan_executor render 截断补数调用)."""
        ...


@runtime_checkable
class DataSourceDriver(Protocol):
    """所有数据源驱动必须实现此协议."""

    db_type: str
    paradigm: str  # "relational" | "document" — 知识挂在实体上, 由 driver 类声明

    async def list_object_names(self, ds: DataSource) -> list[str]:
        """连库列所有表/集合名. 用于反查 (object_name → database) 绑定.

        连接失败抛异常, 由调用方隔离.
        """
        ...

    async def fetch_schema(
        self,
        ds: DataSource,
        target: str | None = None,
    ) -> SchemaSnapshot | list[SchemaSnapshot]:
        ...

    async def inspect_values(
        self,
        ds: DataSource,
        target: str,
        field: str,
        limit: int = 10,
    ) -> list[dict]:
        ...

    async def estimate_cost(
        self,
        ds: DataSource,
        target: str,
        query: dict,
    ) -> CostEstimate:
        ...

    async def execute_query(
        self,
        ds: DataSource,
        target: str,
        query: dict,
    ) -> ExecuteResult:
        ...

    async def health_check(self, ds: DataSource) -> bool:
        ...

    async def fetch_db_profile(self, ds: DataSource) -> dict:
        """连库合成库级画像 (版本/字符集或flavor/对象数量).

        ⚠️ 走一次性临时连接, 不进 ds.id 缓存池 (建源时 ds.id 尚为 None).
        降级语义: 每个子查询独立 try, 抓到几个算几个, 永不抛异常.
        返回 dict 缺某键 = 该项抽取失败, 不影响其他键. profiled_at 始终有.
        """
        ...

    async def fetch_foreign_keys(
        self, ds: DataSource, target: str | None = None,
    ) -> list[dict]:
        """返回外键关系列表. 每项含 from_target/from_field/to_db_type/to_database/
        to_target/to_field/relation_type. 不支持外键的 driver 显式 return []."""
        return []

    async def probe_connectivity(self, ds: DataSource) -> ProbeResult:
        """连库探测连通 + 时区. 一次性临时连接, 不落库. 不探 version/charset."""
        ...
