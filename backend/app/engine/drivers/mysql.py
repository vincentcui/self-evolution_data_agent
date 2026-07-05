"""MySQL 异步驱动 — aiomysql 连接池 + SQL 安全执行."""
from __future__ import annotations

import asyncio
import logging
import re
import time

import aiomysql
import sqlparse

from app.config import settings
from app.engine.drivers._exceptions import (
    ConnectionFailureError,
    PayloadShapeMismatchError,
    QueryTimeoutError,
    UnsafeQueryError,
)
from app.engine.drivers.base import (
    CostEstimate,
    ExecuteMode,
    ExecuteResult,
    FieldDef,
    ProbeResult,
    SchemaSnapshot,
)
from app.models import DataSource
from app.models.base import local_now

log = logging.getLogger(__name__)

_SAFE_FIELD_RE = re.compile(r"^[\w\u4e00-\u9fff]+$")
_DML_DDL_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|REPLACE|GRANT|REVOKE|CALL)\b",
    re.IGNORECASE,
)
# 末尾外层 LIMIT / LIMIT a,b / OFFSET — render/count 剥离 planner 末步行保护用
_OUTER_LIMIT_RE = re.compile(
    r"\s+LIMIT\s+\d+(\s*,\s*\d+)?(\s+OFFSET\s+\d+)?\s*;?\s*$", re.IGNORECASE,
)


class MySQLDriver:
    """aiomysql 连接池驱动, 实现 DataSourceDriver 协议."""

    db_type: str = "mysql"
    paradigm: str = "relational"

    def __init__(self) -> None:
        self._pools: dict[int, aiomysql.Pool] = {}

    async def _get_pool(self, ds: DataSource) -> aiomysql.Pool:
        """获取或创建 ds 对应的连接池."""
        if ds.id is None:
            raise ValueError(
                "未落库的 DataSource (ds.id is None) 不可进连接池缓存; "
                "建源画像请用 fetch_db_profile 的一次性临时连接"
            )
        if ds.id in self._pools:
            pool = self._pools[ds.id]
            if not pool.closed:
                return pool
        try:
            pool = await aiomysql.create_pool(
                host=ds.host,
                port=ds.port,
                user=ds.username,
                password=ds.password,
                db=ds.database,
                maxsize=settings.mysql_pool_max_size,
                pool_recycle=3600,  # noqa: hardcode
                connect_timeout=settings.mysql_pool_timeout_secs,
                autocommit=True,
                charset="utf8mb4",
            )
        except Exception as exc:
            raise ConnectionFailureError(
                f"MySQL 连接失败: {ds.host}:{ds.port}/{ds.database} — {exc}",
                suggestion="检查 host/port/credentials 是否正确",
            ) from exc
        self._pools[ds.id] = pool
        return pool

    # ── fetch_schema ─────────────────────────────────────

    async def fetch_schema(
        self,
        ds: DataSource,
        target: str | None = None,
    ) -> SchemaSnapshot | list[SchemaSnapshot]:
        log.info("[mysql_driver] fetch_schema ds=%d target=%s", ds.id, target)
        pool = await self._get_pool(ds)
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                if target is None:
                    # 列出所有表
                    await cur.execute(
                        "SELECT TABLE_NAME, TABLE_ROWS, TABLE_COMMENT "
                        "FROM INFORMATION_SCHEMA.TABLES "
                        "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'",
                        (ds.database,),
                    )
                    tables = await cur.fetchall()
                    results: list[SchemaSnapshot] = []
                    for t in tables:
                        results.append(
                            SchemaSnapshot(
                                db_type="mysql",
                                database=ds.database,
                                target=t["TABLE_NAME"],
                                description=t.get("TABLE_COMMENT") or "",
                                fields=[],
                                indexes=[],
                                sample_count=t.get("TABLE_ROWS") or 0,
                            )
                        )
                    return results

                # 单表详情
                await cur.execute(
                    "SELECT COLUMN_NAME, COLUMN_TYPE, COLUMN_COMMENT, "
                    "IS_NULLABLE, COLUMN_KEY "
                    "FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                    "ORDER BY ORDINAL_POSITION",
                    (ds.database, target),
                )
                columns = await cur.fetchall()

                fields: list[FieldDef] = []
                for col in columns:
                    fields.append(
                        FieldDef(
                            name=col["COLUMN_NAME"],
                            type=col["COLUMN_TYPE"],
                            description=col.get("COLUMN_COMMENT") or "",
                            indexed=col.get("COLUMN_KEY", "") != "",
                            nullable=col.get("IS_NULLABLE") == "YES",
                        )
                    )

                # 索引信息
                await cur.execute(
                    "SELECT INDEX_NAME, COLUMN_NAME, NON_UNIQUE, SEQ_IN_INDEX "
                    "FROM INFORMATION_SCHEMA.STATISTICS "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                    "ORDER BY INDEX_NAME, SEQ_IN_INDEX",
                    (ds.database, target),
                )
                raw_indexes = await cur.fetchall()
                indexes: list[dict] = []
                idx_map: dict[str, dict] = {}
                for row in raw_indexes:
                    name = row["INDEX_NAME"]
                    if name not in idx_map:
                        idx_map[name] = {
                            "name": name,
                            "unique": row["NON_UNIQUE"] == 0,
                            "columns": [],
                        }
                    idx_map[name]["columns"].append(row["COLUMN_NAME"])
                indexes = list(idx_map.values())

                # 行数估算 + 表注释
                await cur.execute(
                    "SELECT TABLE_ROWS, TABLE_COMMENT FROM INFORMATION_SCHEMA.TABLES "
                    "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s",
                    (ds.database, target),
                )
                row = await cur.fetchone()
                sample_count = (row or {}).get("TABLE_ROWS") or 0
                table_comment = (row or {}).get("TABLE_COMMENT") or ""

                return SchemaSnapshot(
                    db_type="mysql",
                    database=ds.database,
                    target=target,
                    description=table_comment,
                    fields=fields,
                    indexes=indexes,
                    sample_count=sample_count,
                )

    # ── inspect_values ───────────────────────────────────

    async def inspect_values(
        self,
        ds: DataSource,
        target: str,
        field: str,
        limit: int = 10,
    ) -> list[dict]:
        log.info("[mysql_driver] inspect_values ds=%d target=%s field=%s", ds.id, target, field)
        if not _SAFE_FIELD_RE.match(field):
            raise UnsafeQueryError(
                f"字段名不合法: {field!r}",
                suggestion="字段名仅允许字母/数字/下划线/中文",
            )
        if not _SAFE_FIELD_RE.match(target):
            raise UnsafeQueryError(
                f"表名不合法: {target!r}",
                suggestion="表名仅允许字母/数字/下划线/中文",
            )
        pool = await self._get_pool(ds)
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                sql = (
                    f"SELECT `{field}`, COUNT(*) AS cnt "  # noqa: S608
                    f"FROM `{target}` "
                    f"GROUP BY `{field}` "
                    f"ORDER BY cnt DESC LIMIT %s"
                )
                await cur.execute(sql, (limit,))
                return await cur.fetchall()

    # ── estimate_cost ────────────────────────────────────

    async def estimate_cost(
        self,
        ds: DataSource,
        target: str,
        query: dict,
    ) -> CostEstimate:
        log.info("[mysql_driver] estimate_cost ds=%d target=%s", ds.id, target)
        sql = query.get("sql", "")
        if not sql:
            raise PayloadShapeMismatchError(
                "estimate_cost 需要 query.sql",
                suggestion="payload 必须包含 'sql' key",
            )
        pool = await self._get_pool(ds)
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(f"EXPLAIN {sql}")  # noqa: S608
                rows = await cur.fetchall()
                estimated_rows = sum(int(r.get("rows") or 0) for r in rows)
                if estimated_rows > settings.query_cost_total_limit:
                    level = "blocked"
                elif estimated_rows > settings.query_cost_single_layer_limit:
                    level = "high"
                else:
                    level = "ok"
                return CostEstimate(
                    estimated_rows=estimated_rows,
                    warning_level=level,
                    raw_explain={"rows": rows},
                )

    # ── execute_query ────────────────────────────────────

    async def execute_query(
        self,
        ds: DataSource,
        target: str,
        query: dict,
        mode: ExecuteMode = "single",
        batch_size: int = 1000,  # noqa: hardcode
    ) -> ExecuteResult:
        log.info("[mysql_driver] execute_query ds=%d target=%s mode=%s", ds.id, target, mode)
        sql = query.get("sql")
        if not sql:
            raise PayloadShapeMismatchError(
                "execute_query 需要 query.sql",
                suggestion="payload 必须包含 'sql' key",
            )
        self._enforce_select_only(sql)
        sql = self._wrap_by_mode(sql, mode, batch_size)

        pool = await self._get_pool(ds)
        t0 = time.perf_counter()
        try:
            async with pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await asyncio.wait_for(
                        cur.execute(sql),
                        timeout=settings.mysql_query_timeout_secs,
                    )
                    rows = await cur.fetchall()
        except asyncio.TimeoutError as exc:
            raise QueryTimeoutError(
                f"SQL 执行超时 ({settings.mysql_query_timeout_secs}s): {sql[:100]}",
                suggestion="优化查询或增加 IS_MYSQL_QUERY_TIMEOUT_SECS",
            ) from exc
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        truncated = False
        if mode == "single" and len(rows) >= settings.query_row_limit:
            truncated = True
        elif mode == "render" and len(rows) >= settings.render_row_limit:
            truncated = True  # 疑似截断 (executor 补 count 纠正/确证)

        log.info(
            "[mysql_driver] execute_query done ds=%d rows=%d elapsed_ms=%d",
            ds.id,
            len(rows),
            elapsed_ms,
        )
        return ExecuteResult(
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=elapsed_ms,
        )

    # ── health_check ─────────────────────────────────────

    async def health_check(self, ds: DataSource) -> bool:
        try:
            pool = await self._get_pool(ds)
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT 1")
            return True
        except Exception:
            return False

    # ── list_object_names ────────────────────────────────

    async def list_object_names(self, ds: DataSource) -> list[str]:
        """SHOW TABLES → 返回库内全部表名 (sorted)."""
        pool = await self._get_pool(ds)
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SHOW TABLES")
                rows = await cur.fetchall()
                return sorted(r[0] for r in rows)

    # ── fetch_db_profile ─────────────────────────────────

    async def fetch_db_profile(self, ds: DataSource) -> dict:
        """连库合成库级画像. 一次性临时连接, 不进 _pools. 降级安全.

        connected: 连接 + auth (+ MySQL 选定 db) 成功即 True, 与后续元信息抽取解耦.
        建源连通判据用 connected (而非 version 是否抓到), 避免受限账号能连但读不到
        某项元信息时被误拒 (D4 降级语义).
        """
        profile: dict = {"profiled_at": local_now().isoformat(), "connected": False}
        conn = None
        try:
            conn = await aiomysql.connect(
                host=ds.host, port=ds.port, user=ds.username,
                password=ds.password, db=ds.database,
                connect_timeout=settings.mysql_pool_timeout_secs,
                autocommit=True, charset="utf8mb4",
            )
            # 连接 + auth + 选定 db 成功 = 连通 (aiomysql.connect(db=) 会校验库存在)
            profile["connected"] = True
            async with conn.cursor(aiomysql.DictCursor) as cur:
                # 版本
                try:
                    await cur.execute("SELECT VERSION() AS v")
                    row = await cur.fetchone()
                    if row and row.get("v"):
                        profile["version"] = row["v"]
                except Exception:  # noqa: BLE001 — 降级: 该键缺省
                    pass
                # 字符集
                try:
                    await cur.execute(
                        "SELECT DEFAULT_CHARACTER_SET_NAME AS cs "
                        "FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = %s",
                        (ds.database,),
                    )
                    row = await cur.fetchone()
                    if row and row.get("cs"):
                        profile["charset"] = row["cs"]
                except Exception:  # noqa: BLE001
                    pass
                # 对象数量 (只要数字, 不要清单)
                try:
                    await cur.execute(
                        "SELECT COUNT(*) AS n FROM INFORMATION_SCHEMA.TABLES "
                        "WHERE TABLE_SCHEMA = %s AND TABLE_TYPE = 'BASE TABLE'",
                        (ds.database,),
                    )
                    row = await cur.fetchone()
                    profile["object_count"] = int((row or {}).get("n") or 0)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001 — 连不上也返回 (只含 profiled_at)
            log.warning("[mysql_driver] fetch_db_profile failed ds_host=%s: %s", ds.host, exc)
        finally:
            if conn is not None:
                conn.close()
        return profile

    # ── probe_connectivity ───────────────────────────────

    async def probe_connectivity(self, ds: DataSource) -> ProbeResult:
        """一次性临时连接: 探测连通 + 时区. 不进连接池, 不探 version/charset."""
        from app.engine.drivers._timezone import normalize_timezone
        try:
            conn = await aiomysql.connect(
                host=ds.host, port=ds.port, user=ds.username,
                password=ds.password, db=ds.database,
                connect_timeout=settings.mysql_pool_timeout_secs,
                autocommit=True, charset="utf8mb4",
            )
        except Exception as exc:
            return ProbeResult(connected=False, failure_reason=str(exc)[:300])
        try:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(
                    "SELECT @@session.time_zone AS sess, @@system_time_zone AS sys"
                )
                row = await cur.fetchone() or {}
                tz = normalize_timezone(row.get("sess")) or normalize_timezone(row.get("sys"))
                return ProbeResult(connected=True, detected_timezone=tz)
        except Exception as exc:
            return ProbeResult(
                connected=True, detected_timezone=None, failure_reason=str(exc)[:300]
            )
        finally:
            conn.close()

    # ── lifecycle ────────────────────────────────────────

    async def invalidate_pool(self, ds_id: int) -> None:
        """关闭并移除指定 ds 的连接池."""
        pool = self._pools.pop(ds_id, None)
        if pool and not pool.closed:
            pool.close()
            await pool.wait_closed()

    async def close_all(self) -> None:
        """关闭所有连接池."""
        for ds_id in list(self._pools.keys()):
            await self.invalidate_pool(ds_id)

    # ── fetch_foreign_keys ─────────────────────────────────

    async def fetch_foreign_keys(
        self, ds: DataSource, target: str | None = None,
    ) -> list[dict]:
        """查 INFORMATION_SCHEMA 外键 → 列表(try/except 降级,与 Oracle 对称)."""
        try:
            pool = await self._get_pool(ds)
            async with pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    if target is not None:
                        await cur.execute(
                            "SELECT TABLE_NAME, COLUMN_NAME, "
                            "REFERENCED_TABLE_SCHEMA, REFERENCED_TABLE_NAME, "
                            "REFERENCED_COLUMN_NAME "
                            "FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE "
                            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
                            "AND REFERENCED_TABLE_NAME IS NOT NULL "
                            "ORDER BY TABLE_NAME, COLUMN_NAME",
                            (ds.database, target),
                        )
                    else:
                        await cur.execute(
                            "SELECT TABLE_NAME, COLUMN_NAME, "
                            "REFERENCED_TABLE_SCHEMA, REFERENCED_TABLE_NAME, "
                            "REFERENCED_COLUMN_NAME "
                            "FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE "
                            "WHERE TABLE_SCHEMA = %s "
                            "AND REFERENCED_TABLE_NAME IS NOT NULL "
                            "ORDER BY TABLE_NAME, COLUMN_NAME",
                            (ds.database,),
                        )
                    rows = await cur.fetchall()
            return _fk_rows_to_relationships(rows, "mysql")
        except Exception:
            log.warning(
                "[mysql_driver] fetch_foreign_keys failed ds=%d target=%s",
                ds.id, target, exc_info=True,
            )
            return []

    # ── private helpers ──────────────────────────────────

    @staticmethod
    def _enforce_select_only(sql: str) -> None:
        """拒绝非 SELECT 语句, 拒绝 DML/DDL, 拒绝多语句."""
        parsed = sqlparse.parse(sql)
        if not parsed:
            raise UnsafeQueryError("空 SQL", suggestion="提供有效的 SELECT 语句")
        if len(parsed) > 1:
            raise UnsafeQueryError(
                "禁止多语句执行",
                suggestion="每次只允许一条 SQL",
            )
        stmt = parsed[0]  # type: ignore[index]
        stmt_type = stmt.get_type()
        if stmt_type and stmt_type.upper() != "SELECT":
            raise UnsafeQueryError(
                f"仅允许 SELECT, 检测到: {stmt_type}",
                suggestion="移除非查询语句",
            )
        if _DML_DDL_KEYWORDS.search(sql):
            raise UnsafeQueryError(
                "SQL 包含禁止的 DML/DDL 关键字",
                suggestion="仅允许 SELECT 查询",
            )

    def strip_outer_row_limit(self, sql: str) -> str:
        """公开行数保护剥离方法，供 plan_executor render/count 路径调用."""
        return self._strip_outer_limit(sql)

    @staticmethod
    def _strip_outer_limit(sql: str) -> str:
        """剥离末尾外层 LIMIT/LIMIT a,b/OFFSET (planner 末步行保护).

        仅尾部一处, 不动子查询内 LIMIT. render mode + render-count 共用.
        """
        return _OUTER_LIMIT_RE.sub("", sql.rstrip().rstrip(";")).rstrip()

    @staticmethod
    def _wrap_by_mode(sql: str, mode: ExecuteMode, batch_size: int) -> str:
        """按 mode 包装 SQL (添加 LIMIT). 已有 LIMIT 时不重复添加.

        render 例外: 不短路 has_limit, 而是剥离末步外层 LIMIT 后 override 为
        render_row_limit (渲染源行上限唯一所有者, 见 §4.6).
        """
        # 去除尾部分号
        sql = sql.rstrip().rstrip(";")
        has_limit = "LIMIT" in sql.upper()
        if mode == "probe":
            return sql if has_limit else f"{sql} LIMIT 10"
        elif mode == "count":
            return f"SELECT COUNT(*) AS cnt FROM ({sql}) AS _sub"
        elif mode == "batched":
            return sql if has_limit else f"{sql} LIMIT {batch_size}"
        elif mode == "render":
            # 剥离 planner 末步外层 LIMIT → 注入 render_row_limit (唯一所有者)
            base = MySQLDriver._strip_outer_limit(sql)
            return f"{base} LIMIT {settings.render_row_limit}"
        else:
            # single — 使用全局 row_limit (已有 LIMIT 时不覆盖)
            return sql if has_limit else f"{sql} LIMIT {settings.query_row_limit}"


def _fk_rows_to_relationships(
    rows: list[dict], db_type: str,
) -> list[dict]:
    """把 INFORMATION_SCHEMA 行映射为 canonical relationship 7 键."""
    return [
        {
            "from_target": r["TABLE_NAME"],
            "from_field": r["COLUMN_NAME"],
            "to_db_type": db_type,
            "to_database": r["REFERENCED_TABLE_SCHEMA"],
            "to_target": r["REFERENCED_TABLE_NAME"],
            "to_field": r["REFERENCED_COLUMN_NAME"],
            "relation_type": "many_to_one",
        }
        for r in rows
    ]
