"""Oracle 驱动 — 双模式支持 (Thin / Thick).

模式选择（进程启动时一次性决定）:
- Thin mode (默认): 直连, 无需 Oracle Instant Client, 支持 Oracle 12.1+.
  oracledb 的 async API 原生可用.
- Thick mode: 需要 Oracle Instant Client; 支持 Oracle 11g+.
  oracledb 在 Thick mode 下不提供 async API — 使用同步连接池
  + asyncio.run_in_executor() 把 DB 操作推到线程池.

配置: IS_ORACLE_THICK_MODE_LIB_DIR (非空 → 启用 Thick mode)

其余约束:
- DSN: Easy Connect 格式 {host}:{port}/{service_name}.
- 可见对象: 当前登录用户 schema 下的表 (USER_* 视图).
- 只读 SELECT; 禁止 DML/DDL/PL/SQL/多语句.
- 行数保护: 统一使用外层 ROWNUM 包装, 跨版本兼容.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import oracledb  # type: ignore[import-untyped]
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


# ── 进程级 Thick mode 初始化 ──────────────────────────────────

def _maybe_init_thick_mode() -> None:
    """进程启动时初始化 Oracle 模式（仅执行一次）。"""
    lib_dir = settings.oracle_thick_mode_lib_dir.strip()
    if not lib_dir:
        log.info(
            "[oracle_driver] Thin mode（支持 Oracle 12.1+）。"
            "如需连接 Oracle 11g，请设置 IS_ORACLE_THICK_MODE_LIB_DIR。"
        )
        return
    try:
        oracledb.init_oracle_client(lib_dir=lib_dir)
        log.info("[oracle_driver] Thick mode 启用 lib_dir=%s — 支持 Oracle 11g+", lib_dir)
    except oracledb.ProgrammingError as e:
        if "already been called" in str(e).lower() or "DPY-1" in str(e):
            log.debug("[oracle_driver] Thick mode already initialized: %s", e)
        else:
            log.warning("[oracle_driver] init_oracle_client 失败: %s", e)
    except Exception as e:
        log.warning(
            "[oracle_driver] Thick mode 初始化失败 lib_dir=%s: %s — 退回 Thin mode",
            lib_dir,
            e,
        )


_maybe_init_thick_mode()


def _is_thick() -> bool:
    """当前进程是否处于 Thick mode。"""
    return not oracledb.is_thin_mode()


# ── SQL 安全 ──────────────────────────────────────────────────

_SAFE_IDENT_RE = re.compile(r"^[\w一-鿿]+$")
_DML_DDL_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|MERGE|"
    r"GRANT|REVOKE|CALL|BEGIN|DECLARE|EXECUTE)\b",
    re.IGNORECASE,
)

# ── 行数保护剥离 ──────────────────────────────────────────────

_ORACLE_FETCH_TAIL_RE = re.compile(
    r"\s+(OFFSET\s+\d+\s+ROWS\s+)?FETCH\s+(FIRST|NEXT)\s+\d+\s+ROWS\s+ONLY\s*$",
    re.IGNORECASE,
)
_ORACLE_ROWNUM_WRAPPER_RE = re.compile(
    r"^\s*SELECT\s+\*\s+FROM\s*\((?P<body>.*)\)\s+WHERE\s+ROWNUM\s*<=\s*\d+\s*$",
    re.IGNORECASE | re.DOTALL,
)


def _normalize_sql(sql: str) -> str:
    return sql.strip().rstrip(";").strip()


def _strip_outer_row_limit_impl(sql: str) -> str:
    """只剥离最外层行数保护，不处理子查询里的 FETCH/ROWNUM。"""
    current = _normalize_sql(sql)
    while True:
        next_sql = _ORACLE_FETCH_TAIL_RE.sub("", current).strip()
        m = _ORACLE_ROWNUM_WRAPPER_RE.match(next_sql)
        if m:
            next_sql = m.group("body").strip()
        if next_sql == current:
            return current
        current = next_sql


def _rownum_wrap(sql: str, limit: int) -> str:
    base = _strip_outer_row_limit_impl(sql)
    return f"SELECT * FROM ({base}) WHERE ROWNUM <= {int(limit)}"


def _cursor_to_dicts(cursor: Any, rows: list) -> list[dict]:
    """将 oracledb tuple rows 转换为 list[dict]，列名小写。"""
    if not cursor.description:
        return []
    col_names = [col[0].lower() for col in cursor.description]
    return [dict(zip(col_names, row)) for row in rows]


def _oracle_fk_rows_to_relationships(
    rows: list[dict], db_type: str,
) -> list[dict]:
    """Oracle FK rows → canonical relationship 7 键."""
    return [
        {
            "from_target": r["table_name"],
            "from_field": r["column_name"],
            "to_db_type": db_type,
            "to_database": r["referenced_owner"],
            "to_target": r["referenced_table_name"],
            "to_field": r["referenced_column_name"],
            "relation_type": "many_to_one",
        }
        for r in rows
    ]


# ── OracleDriver ──────────────────────────────────────────────

class OracleDriver:
    """Oracle 驱动，自动适配 Thin / Thick mode。"""

    db_type: str = "oracle"
    paradigm: str = "relational"

    def __init__(self) -> None:
        # Thin mode: AsyncConnectionPool
        self._async_pools: dict[int, oracledb.AsyncConnectionPool] = {}
        # Thick mode: 同步 ConnectionPool + 线程执行器
        self._sync_pools: dict[int, oracledb.ConnectionPool] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=settings.oracle_pool_max_size,
            thread_name_prefix="oracle_thick",
        )

    # ── 连接池管理 ────────────────────────────────────────────

    def _dsn(self, ds: DataSource) -> str:
        return f"{ds.host}:{ds.port}/{ds.database}"

    async def _get_async_pool(self, ds: DataSource) -> oracledb.AsyncConnectionPool:
        """Thin mode: 获取或创建异步连接池。"""
        if ds.id is None:
            raise ValueError("ds.id is None — 建源画像请用 fetch_db_profile")
        if ds.id in self._async_pools:
            # 缓存命中直接返回，不判断 closed/opened
            # （oracledb 4.x AsyncConnectionPool 无 closed 属性；
            #   若池已不可用，下游操作会抛异常，届时会重建）
            return self._async_pools[ds.id]
        try:
            # create_pool_async 是同步函数，返回 AsyncConnectionPool 对象，不能 await
            pool = oracledb.create_pool_async(
                user=ds.username, password=ds.password, dsn=self._dsn(ds),
                min=settings.oracle_pool_min_size,
                max=settings.oracle_pool_max_size,
                increment=settings.oracle_pool_increment,
                timeout=settings.oracle_pool_timeout_secs,
                wait_timeout=settings.oracle_pool_timeout_secs * 1000,
                tcp_connect_timeout=settings.oracle_connect_timeout_secs,
            )
        except Exception as exc:
            raise ConnectionFailureError(
                f"Oracle 连接失败: {self._dsn(ds)} — {exc}",
                suggestion="检查 host/port/service_name/credentials 及 Oracle 12.1+ 是否运行",
            ) from exc
        self._async_pools[ds.id] = pool
        return pool

    def _get_sync_pool(self, ds: DataSource) -> oracledb.ConnectionPool:
        """同步连接池 — Thick/Thin 均可用（oracledb.create_pool 不区分模式）。"""
        if ds.id is None:
            raise ValueError("ds.id is None — 建源画像请用 fetch_db_profile")
        if ds.id in self._sync_pools:
            return self._sync_pools[ds.id]
        try:
            pool = oracledb.create_pool(
                user=ds.username, password=ds.password, dsn=self._dsn(ds),
                min=settings.oracle_pool_min_size,
                max=settings.oracle_pool_max_size,
                increment=settings.oracle_pool_increment,
                timeout=settings.oracle_pool_timeout_secs,
                wait_timeout=settings.oracle_pool_timeout_secs * 1000,
                tcp_connect_timeout=settings.oracle_connect_timeout_secs,
            )
        except Exception as exc:
            raise ConnectionFailureError(
                f"Oracle 连接失败: {self._dsn(ds)} — {exc}",
                suggestion=(
                    "检查 host/port/service_name/credentials 及 Oracle Instant Client 是否安装"
                ),
            ) from exc
        self._sync_pools[ds.id] = pool
        return pool

    async def _run_in_executor(self, func, *args):
        """把同步 DB 操作推入线程池（Thick mode 专用）。"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, func, *args)

    # ── fetch_schema ──────────────────────────────────────────

    async def fetch_schema(
        self, ds: DataSource, target: str | None = None,
    ) -> SchemaSnapshot | list[SchemaSnapshot]:
        """Thick/Thin 统一走 executor + 同步连接，消除两份重复实现。"""
        log.info("[oracle_driver] fetch_schema ds=%s target=%s", ds.id, target)
        return await self._run_in_executor(self._fetch_schema_sync, ds, target)

    def _fetch_schema_sync(
        self,
        ds: DataSource,
        target: str | None,
    ) -> SchemaSnapshot | list[SchemaSnapshot]:
        """单份同步实现，在线程执行器中运行。

        ds.id 不为 None（已落库的数据源）时，Thick/Thin 统一走 _get_sync_pool 复用连接，
        避免全库刷新时 N+1 次建连（fetch_schema(None) + N × fetch_schema(target)）。
        ds.id 为 None（建源画像场景）时退化为一次性连接。
        """
        if ds.id is not None:
            # 复用同步连接池（oracledb.create_pool 在 Thin/Thick 模式下均有效）
            pool = self._get_sync_pool(ds)
            with pool.acquire() as conn:
                return self._schema_queries(conn, ds, target)
        # ds.id=None（建源前画像）→ 一次性连接
        conn = oracledb.connect(
            user=ds.username, password=ds.password, dsn=self._dsn(ds),
            tcp_connect_timeout=settings.oracle_connect_timeout_secs,
        )
        try:
            return self._schema_queries(conn, ds, target)
        finally:
            conn.close()

    @staticmethod
    def _schema_queries(
        conn: Any,
        ds: DataSource,
        target: str | None,
    ) -> SchemaSnapshot | list[SchemaSnapshot]:
        """单份 Oracle schema 查询逻辑，供 Thick/Thin 共用。"""
        cur = conn.cursor()
        if target is None:
            cur.execute(
                "SELECT t.TABLE_NAME, c.COMMENTS "
                "FROM USER_TABLES t "
                "LEFT JOIN USER_TAB_COMMENTS c ON c.TABLE_NAME = t.TABLE_NAME "
                "ORDER BY t.TABLE_NAME"
            )
            rows = cur.fetchall()
            col_names = [col[0].lower() for col in cur.description]
            cur.close()
            return [
                SchemaSnapshot(
                    db_type="oracle", database=ds.database,
                    target=dict(zip(col_names, r))["table_name"],
                    description=dict(zip(col_names, r)).get("comments") or "",
                    fields=[], indexes=[], sample_count=0,
                )
                for r in rows
            ]

        target_upper = target.upper()
        cur.execute(
            "SELECT c.COLUMN_NAME, c.DATA_TYPE, c.NULLABLE, cm.COMMENTS "
            "FROM USER_TAB_COLUMNS c "
            "LEFT JOIN USER_COL_COMMENTS cm "
            "       ON cm.TABLE_NAME = c.TABLE_NAME AND cm.COLUMN_NAME = c.COLUMN_NAME "
            "WHERE c.TABLE_NAME = :tbl ORDER BY c.COLUMN_ID",
            tbl=target_upper,
        )
        col_rows = cur.fetchall()
        col_desc = [col[0].lower() for col in cur.description]
        fields: list[FieldDef] = [
            FieldDef(
                name=dict(zip(col_desc, r))["column_name"],
                type=dict(zip(col_desc, r))["data_type"],
                description=dict(zip(col_desc, r)).get("comments") or "",
                indexed=False,
                nullable=dict(zip(col_desc, r)).get("nullable", "Y") == "Y",
            )
            for r in col_rows
        ]
        cur.execute(
            "SELECT ic.INDEX_NAME, ic.COLUMN_NAME, i.UNIQUENESS "
            "FROM USER_IND_COLUMNS ic JOIN USER_INDEXES i ON i.INDEX_NAME = ic.INDEX_NAME "
            "WHERE ic.TABLE_NAME = :tbl ORDER BY ic.INDEX_NAME, ic.COLUMN_POSITION",
            tbl=target_upper,
        )
        idx_rows = cur.fetchall()
        idx_desc = [col[0].lower() for col in cur.description]
        idx_map: dict[str, dict] = {}
        indexed_cols: set[str] = set()
        for r in idx_rows:
            rd = dict(zip(idx_desc, r))
            n = rd["index_name"]
            if n not in idx_map:
                idx_map[n] = {
                    "name": n,
                    "unique": rd.get("uniqueness") == "UNIQUE",
                    "columns": [],
                }
            idx_map[n]["columns"].append(rd["column_name"])
            indexed_cols.add(rd["column_name"])
        indexes = list(idx_map.values())
        for f in fields:
            f["indexed"] = f["name"] in indexed_cols

        cur.execute(
            "SELECT NVL(NUM_ROWS, 0), "
            "(SELECT COMMENTS FROM USER_TAB_COMMENTS WHERE TABLE_NAME = :tbl) "
            "FROM USER_TABLES WHERE TABLE_NAME = :tbl",
            tbl=target_upper,
        )
        meta = cur.fetchone()
        sample_count = int((meta or (0, ""))[0] or 0)
        table_comment = str((meta or (0, ""))[1] or "")
        cur.close()
        return SchemaSnapshot(
            db_type="oracle",
            database=ds.database,
            target=target,
            description=table_comment,
            fields=fields,
            indexes=indexes,
            sample_count=sample_count,
        )

    # ── inspect_values ────────────────────────────────────────

    async def inspect_values(
        self,
        ds: DataSource,
        target: str,
        field: str,
        limit: int = 10,
    ) -> list[dict]:
        """Thick/Thin 统一走 executor + sync pool, 消除双份实现。"""
        log.info(
            "[oracle_driver] inspect_values ds=%s target=%s field=%s",
            ds.id, target, field,
        )
        for name, val in [("字段", field), ("表", target)]:
            if not _SAFE_IDENT_RE.match(val):
                raise UnsafeQueryError(
                    f"{name}名不合法: {val!r}",
                    suggestion=f"{name}名仅允许字母/数字/下划线/中文",
                )
        return await self._run_in_executor(self._inspect_values_sync, ds, target, field, limit)

    def _inspect_values_sync(
        self, ds: DataSource, target: str, field: str, limit: int,
    ) -> list[dict]:
        sql = (
            f'SELECT * FROM ('
            f'  SELECT "{field.upper()}" AS val, COUNT(*) AS cnt '
            f'  FROM "{target.upper()}" '
            f'  GROUP BY "{field.upper()}" ORDER BY cnt DESC'
            f') WHERE ROWNUM <= :lim'
        )
        pool = self._get_sync_pool(ds)
        with pool.acquire() as conn:
            cur = conn.cursor()
            cur.execute(sql, lim=int(limit))
            rows = cur.fetchall()
            return _cursor_to_dicts(cur, rows)

    # ── estimate_cost ─────────────────────────────────────────

    async def estimate_cost(self, ds: DataSource, target: str, query: dict) -> CostEstimate:
        """Thick/Thin 统一走 executor + sync pool, 消除双份实现。"""
        sql = query.get("sql", "")
        if not sql:
            raise PayloadShapeMismatchError(
                "estimate_cost 需要 query.sql",
                suggestion="payload 必须包含 'sql' key",
            )
        try:
            estimated_rows = await self._run_in_executor(self._estimate_cost_sync, ds, sql)
            if estimated_rows > settings.query_cost_total_limit:
                level = "blocked"
            elif estimated_rows > settings.query_cost_single_layer_limit:
                level = "high"
            else:
                level = "ok"
            return CostEstimate(
                estimated_rows=estimated_rows,
                warning_level=level,
                raw_explain={"rows": estimated_rows},
            )
        except Exception as exc:
            log.warning(
                "[oracle_driver] estimate_cost degraded ds=%s: %s "
                "(EXPLAIN PLAN 不可用，返回 warning_level=high 让调用方保守处理)",
                ds.id, exc,
            )
            return CostEstimate(
                estimated_rows=0,
                warning_level="high",
                raw_explain={
                    "error": str(exc)[:200],
                    "degraded": True,
                    "note": "EXPLAIN PLAN 不可用",
                },
            )

    def _estimate_cost_sync(self, ds: DataSource, sql: str) -> int:
        stmt_id = uuid.uuid4().hex[:16]
        explain_sql = (
            "EXPLAIN PLAN SET STATEMENT_ID = "
            f"'{stmt_id}' FOR {_normalize_sql(sql)}"
        )
        plan_rows_sql = (
            "SELECT NVL(SUM(CARDINALITY), 0) FROM PLAN_TABLE "
            "WHERE STATEMENT_ID = :sid"
        )
        delete_plan_sql = "DELETE FROM PLAN_TABLE WHERE STATEMENT_ID = :sid"
        pool = self._get_sync_pool(ds)
        with pool.acquire() as conn:
            cur = conn.cursor()
            cur.execute(explain_sql)  # noqa: S608
            cur.execute(plan_rows_sql, sid=stmt_id)
            row = cur.fetchone()
            estimated = int((row or (0,))[0] or 0)
            try:
                cur.execute(delete_plan_sql, sid=stmt_id)
                conn.commit()
            except Exception:
                pass
            return estimated

    # ── execute_query ─────────────────────────────────────────

    async def execute_query(
        self,
        ds: DataSource,
        target: str,
        query: dict,
        mode: ExecuteMode = "single",
        batch_size: int = 1000,  # noqa: hardcode
    ) -> ExecuteResult:
        """Thick/Thin 统一走 executor + sync pool, 消除双份实现。"""
        log.info(
            "[oracle_driver] execute_query ds=%s target=%s mode=%s thick=%s",
            ds.id, target, mode, _is_thick(),
        )
        sql = query.get("sql")
        if not sql:
            raise PayloadShapeMismatchError(
                "execute_query 需要 query.sql",
                suggestion="payload 必须包含 'sql' key",
            )
        self._enforce_select_only(sql)
        sql = self._wrap_by_mode(sql, mode, batch_size)

        t0 = time.perf_counter()
        try:
            rows = await asyncio.wait_for(
                self._run_in_executor(self._execute_query_sync, ds, sql),
                timeout=settings.oracle_query_timeout_secs,
            )
        except asyncio.TimeoutError as exc:
            raise QueryTimeoutError(
                f"SQL 执行超时 ({settings.oracle_query_timeout_secs}s): {sql[:100]}",
                suggestion="优化查询或增加 IS_ORACLE_QUERY_TIMEOUT_SECS",
            ) from exc

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        truncated = (
            (mode == "single" and len(rows) >= settings.query_row_limit)
            or (mode == "render" and len(rows) >= settings.render_row_limit)
        )
        log.info(
            "[oracle_driver] execute_query done ds=%s rows=%d elapsed_ms=%d",
            ds.id, len(rows), elapsed_ms,
        )
        return ExecuteResult(
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            elapsed_ms=elapsed_ms,
        )

    def _execute_query_sync(self, ds: DataSource, sql: str) -> list[dict]:
        pool = self._get_sync_pool(ds)
        with pool.acquire() as conn:
            cur = conn.cursor()
            cur.execute(sql)
            rows_raw = cur.fetchall()
            return _cursor_to_dicts(cur, rows_raw)

    # ── health_check ──────────────────────────────────────────

    async def health_check(self, ds: DataSource) -> bool:
        """Thick/Thin 统一走 executor + sync pool, 消除双份实现。"""
        try:
            await self._run_in_executor(self._health_check_sync, ds)
            return True
        except Exception:
            return False

    def _health_check_sync(self, ds: DataSource) -> None:
        pool = self._get_sync_pool(ds)
        with pool.acquire() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM DUAL")
            cur.fetchone()

    # ── list_object_names ──────────────────────────────────────

    async def list_object_names(self, ds: DataSource) -> list[str]:
        """SELECT TABLE_NAME FROM USER_TABLES → sorted list."""
        return await self._run_in_executor(self._list_object_names_sync, ds)

    def _list_object_names_sync(self, ds: DataSource) -> list[str]:
        pool = self._get_sync_pool(ds)
        with pool.acquire() as conn:
            cur = conn.cursor()
            cur.execute("SELECT TABLE_NAME FROM USER_TABLES ORDER BY TABLE_NAME")
            rows = cur.fetchall()
            return sorted(r[0] for r in rows)

    # ── probe_connectivity ───────────────────────────────

    async def probe_connectivity(self, ds: DataSource) -> ProbeResult:
        """一次性临时连接: 探测连通 + 时区. 走 executor 兼容 Thick/Thin."""
        return await self._run_in_executor(self._probe_connectivity_sync, ds)

    def _probe_connectivity_sync(self, ds: DataSource) -> ProbeResult:
        from app.engine.drivers._timezone import normalize_timezone
        try:
            conn = oracledb.connect(
                user=ds.username, password=ds.password, dsn=self._dsn(ds),
                tcp_connect_timeout=settings.oracle_connect_timeout_secs,
            )
        except Exception as exc:
            return ProbeResult(connected=False, failure_reason=str(exc)[:300])
        try:
            cur = conn.cursor()
            cur.execute("SELECT SESSIONTIMEZONE FROM DUAL")
            raw = str(cur.fetchone()[0])
            return ProbeResult(connected=True, detected_timezone=normalize_timezone(raw))
        except Exception as exc:
            return ProbeResult(
                connected=True, detected_timezone=None, failure_reason=str(exc)[:300]
            )
        finally:
            conn.close()

    # ── fetch_db_profile ──────────────────────────────────────

    async def fetch_db_profile(self, ds: DataSource) -> dict:
        """一次性临时连接 (不进连接池), Thick/Thin 统一走 executor。"""
        return await self._run_in_executor(self._fetch_db_profile_sync, ds)

    def _fetch_db_profile_sync(self, ds: DataSource) -> dict:
        p: dict = {"profiled_at": local_now().isoformat(), "connected": False}
        dsn = self._dsn(ds)
        conn = None
        try:
            conn = oracledb.connect(
                user=ds.username, password=ds.password, dsn=dsn,
                tcp_connect_timeout=settings.oracle_connect_timeout_secs,
            )
            p["connected"] = True
            cur = conn.cursor()
            for stmt, key, extractor in [
                ("SELECT BANNER FROM V$VERSION WHERE ROWNUM = 1", "version",
                 lambda r: str(r[0])),
                ("SELECT USER FROM DUAL", "schema", lambda r: str(r[0])),
                ("SELECT COUNT(*) FROM USER_TABLES", "object_count",
                 lambda r: int(r[0] or 0)),
                ("SELECT VALUE FROM NLS_DATABASE_PARAMETERS WHERE PARAMETER='NLS_CHARACTERSET'",
                 "charset", lambda r: str(r[0])),
                ("SELECT VALUE FROM NLS_DATABASE_PARAMETERS "
                 "WHERE PARAMETER='NLS_NCHAR_CHARACTERSET'",
                 "nchar_charset", lambda r: str(r[0])),
            ]:
                try:
                    cur.execute(stmt)
                    row = cur.fetchone()
                    if row:
                        p[key] = extractor(row)
                except Exception:
                    pass
            cur.close()
        except Exception as exc:
            p["error"] = str(exc)[:300]
            log.warning("[oracle_driver] fetch_db_profile failed host=%s: %s", ds.host, exc)
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
        return p

    # ── fetch_foreign_keys ─────────────────────────────────

    async def fetch_foreign_keys(
        self, ds: DataSource, target: str | None = None,
    ) -> list[dict]:
        """查 USER_CONSTRAINTS(type='R') → FK 列表. 降级返 []."""
        try:
            return await self._run_in_executor(
                self._fetch_foreign_keys_sync, ds, target,
            )
        except Exception:
            log.warning(
                "[oracle_driver] fetch_foreign_keys failed ds=%d target=%s",
                ds.id, target, exc_info=True,
            )
            return []

    def _fetch_foreign_keys_sync(
        self, ds: DataSource, target: str | None,
    ) -> list[dict]:
        """同步取 FK. ds.id 不为 None 走连接池, None 走一次性连接."""
        if ds.id is not None:
            pool = self._get_sync_pool(ds)
            with pool.acquire() as conn:
                return self._fk_queries(conn, ds, target)
        conn = oracledb.connect(
            user=ds.username, password=ds.password, dsn=self._dsn(ds),
            tcp_connect_timeout=settings.oracle_connect_timeout_secs,
        )
        try:
            return self._fk_queries(conn, ds, target)
        finally:
            conn.close()

    @staticmethod
    def _fk_queries(conn, ds: DataSource, target: str | None) -> list[dict]:
        cur = conn.cursor()
        if target is not None:
            cur.execute(
                "SELECT a.TABLE_NAME, a.COLUMN_NAME, "
                "c_pk.OWNER AS REFERENCED_OWNER, "
                "c_pk.TABLE_NAME AS REFERENCED_TABLE_NAME, "
                "c_pk.COLUMN_NAME AS REFERENCED_COLUMN_NAME "
                "FROM USER_CONS_COLUMNS a "
                "JOIN USER_CONSTRAINTS r ON r.CONSTRAINT_NAME = a.CONSTRAINT_NAME "
                "   AND r.CONSTRAINT_TYPE = 'R' "
                "JOIN USER_CONS_COLUMNS c_pk ON c_pk.CONSTRAINT_NAME = r.R_CONSTRAINT_NAME "
                "   AND c_pk.POSITION = a.POSITION "
                "WHERE a.TABLE_NAME = :target "
                "ORDER BY a.TABLE_NAME, a.COLUMN_NAME",
                target=target,
            )
        else:
            cur.execute(
                "SELECT a.TABLE_NAME, a.COLUMN_NAME, "
                "c_pk.OWNER AS REFERENCED_OWNER, "
                "c_pk.TABLE_NAME AS REFERENCED_TABLE_NAME, "
                "c_pk.COLUMN_NAME AS REFERENCED_COLUMN_NAME "
                "FROM USER_CONS_COLUMNS a "
                "JOIN USER_CONSTRAINTS r ON r.CONSTRAINT_NAME = a.CONSTRAINT_NAME "
                "   AND r.CONSTRAINT_TYPE = 'R' "
                "JOIN USER_CONS_COLUMNS c_pk ON c_pk.CONSTRAINT_NAME = r.R_CONSTRAINT_NAME "
                "   AND c_pk.POSITION = a.POSITION "
                "ORDER BY a.TABLE_NAME, a.COLUMN_NAME"
            )
        rows = _cursor_to_dicts(cur, cur.fetchall())
        return _oracle_fk_rows_to_relationships(rows, "oracle")

    # ── lifecycle ─────────────────────────────────────────────

    async def invalidate_pool(self, ds_id: int) -> None:
        """关闭并移除指定 ds 的连接池。"""
        pool = self._async_pools.pop(ds_id, None)
        if pool:
            try:
                await pool.close()
            except Exception:  # noqa: BLE001 — 已关闭或不可用，忽略
                pass
        sync_pool = self._sync_pools.pop(ds_id, None)
        if sync_pool:
            def _close():
                try:
                    sync_pool.close()
                except Exception:
                    pass
            await self._run_in_executor(_close)

    async def close_all(self) -> None:
        """关闭所有连接池，关闭线程池。"""
        for ds_id in list(self._async_pools.keys()):
            await self.invalidate_pool(ds_id)
        for ds_id in list(self._sync_pools.keys()):
            await self.invalidate_pool(ds_id)
        self._executor.shutdown(wait=False)

    # ── SqlDataSourceDriver 协议 ──────────────────────────────

    def strip_outer_row_limit(self, sql: str) -> str:
        """剥离最外层行数保护，供 executor render/count 路径调用。"""
        return _strip_outer_row_limit_impl(sql)

    # ── private helpers ───────────────────────────────────────

    @staticmethod
    def _enforce_select_only(sql: str) -> None:
        normalized = _normalize_sql(sql)
        if not normalized:
            raise UnsafeQueryError("空 SQL", suggestion="提供有效的 SELECT 语句")
        parsed = sqlparse.parse(normalized)
        if len(parsed) > 1:
            raise UnsafeQueryError("禁止多语句执行", suggestion="每次只允许一条 SQL")
        stmt_type = parsed[0].get_type()  # type: ignore[index]
        if stmt_type and stmt_type.upper() != "SELECT":
            raise UnsafeQueryError(
                f"仅允许 SELECT，检测到: {stmt_type}",
                suggestion="移除非查询语句",
            )
        if _DML_DDL_KEYWORDS.search(normalized):
            raise UnsafeQueryError("SQL 包含禁止的 DML/DDL 关键字", suggestion="仅允许 SELECT 查询")

    @staticmethod
    def _wrap_by_mode(sql: str, mode: ExecuteMode, batch_size: int) -> str:
        normalized = _normalize_sql(sql)
        base = _strip_outer_row_limit_impl(normalized)
        has_limit = base != normalized
        if mode == "count":
            return f"SELECT COUNT(*) AS cnt FROM ({base})"
        if mode == "render":
            return _rownum_wrap(base, settings.render_row_limit)
        if mode == "probe":
            return normalized if has_limit else _rownum_wrap(normalized, 10)
        if mode == "batched":
            return normalized if has_limit else _rownum_wrap(normalized, batch_size)
        return normalized if has_limit else _rownum_wrap(normalized, settings.query_row_limit)
