"""OracleDriver 单元测试 — _apply_cap 三态 / _strip_outer_row_limit_impl / _enforce_select_only.

不依赖真实 Oracle 连接; 仅测试纯函数逻辑.

execute-query-cap-contract (2026-07-11): mode 已删, `_wrap_by_mode`/`_apply_rownum_limit`
退役 → 三态收敛到 `BaseSqlDriver._apply_cap` (default_limit / hard_ceiling 两数字).
per-mode 分级测删除 (功能消失, 新契约由 test_sql_driver_cap_contract 覆盖);
保留 strip/enforce/cursor/协议/fetch_db_profile 测 + 尾注释击穿词法硬化回归防线.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.config import settings
from app.engine.drivers._exceptions import UnsafeQueryError
from app.engine.drivers.oracle import (
    OracleDriver,
    _cursor_to_dicts,
    _extract_outer_rownum_value,
    _normalize_sql,
    _strip_outer_row_limit_impl,
)

_DRV = OracleDriver()

# ══════════════════════════════════════════════════════════════════════════════
#  _strip_outer_row_limit_impl
# ══════════════════════════════════════════════════════════════════════════════

def test_strip_rownum_wrapper():
    sql = "SELECT * FROM (SELECT id FROM t) WHERE ROWNUM <= 100"
    assert _strip_outer_row_limit_impl(sql) == "SELECT id FROM t"


def test_strip_fetch_first_tail():
    sql = "SELECT id FROM t FETCH FIRST 100 ROWS ONLY"
    assert _strip_outer_row_limit_impl(sql) == "SELECT id FROM t"


def test_strip_fetch_first_with_offset():
    sql = "SELECT id FROM t OFFSET 10 ROWS FETCH FIRST 100 ROWS ONLY"
    assert _strip_outer_row_limit_impl(sql) == "SELECT id FROM t"


def test_strip_nested_rownum_strips_all_executor_layers():
    """executor 注入多层 ROWNUM wrapper 时, 循环剥到核心 SQL.

    实际场景: executor 先包一层 render_row_limit, 再包一层 query_row_limit.
    strip 应将两层都剥掉, 露出原始 SQL.
    执行路径中 SELECT * FROM (<body>) WHERE ROWNUM <= n 模式是 executor 注入的,
    用户 SQL 不会直接写这种模式.
    """
    sql = "SELECT * FROM (SELECT * FROM (SELECT id FROM t) WHERE ROWNUM <= 5) WHERE ROWNUM <= 100"
    result = _strip_outer_row_limit_impl(sql)
    # 两层 ROWNUM wrapper 都应被剥离
    assert result == "SELECT id FROM t"
    assert "ROWNUM" not in result


def test_strip_no_limit_is_noop():
    sql = "SELECT id FROM t WHERE status = 1"
    assert _strip_outer_row_limit_impl(sql) == sql


def test_strip_trailing_semicolon_removed():
    sql = "SELECT id FROM t FETCH FIRST 10 ROWS ONLY;"
    assert _strip_outer_row_limit_impl(sql) == "SELECT id FROM t"


# ══════════════════════════════════════════════════════════════════════════════
#  OracleDriver._apply_cap — 三态行数保护 (ROWNUM/FETCH)
# ══════════════════════════════════════════════════════════════════════════════

def test_apply_cap_no_limit_injects_default():
    sql, applied = _DRV._apply_cap("SELECT id FROM t")
    assert applied == settings.default_limit
    assert f"ROWNUM <= {settings.default_limit}" in sql


def test_apply_cap_under_ceiling_preserved():
    # FETCH 10 ≤ ceiling → 保留原值 (尊重 LLM 小样本意图)
    sql, applied = _DRV._apply_cap("SELECT id FROM t FETCH FIRST 10 ROWS ONLY")
    assert applied == 10
    assert "FETCH FIRST 10" in sql


def test_apply_cap_over_ceiling_rewrapped():
    # FETCH 100000 > ceiling → strip + ROWNUM ceiling
    sql, applied = _DRV._apply_cap("SELECT id FROM t FETCH FIRST 100000 ROWS ONLY")
    assert applied == settings.hard_ceiling
    assert f"ROWNUM <= {settings.hard_ceiling}" in sql
    assert "100000" not in sql
    assert "FETCH FIRST" not in sql


def test_apply_cap_existing_rownum_under_ceiling_preserved():
    sql, applied = _DRV._apply_cap("SELECT * FROM (SELECT id FROM t) WHERE ROWNUM <= 500")
    assert applied == 500
    assert "ROWNUM <= 500" in sql


# ── _extract_outer_rownum_value (供 _apply_cap 方言钩子) ──
def test_oracle_extract_rownum_wrapper_value():
    sql = "SELECT * FROM (SELECT a FROM t) WHERE ROWNUM <= 500"
    assert _extract_outer_rownum_value(sql) == 500


def test_oracle_extract_fetch_first_value():
    sql = "SELECT a FROM t FETCH FIRST 5000 ROWS ONLY"
    assert _extract_outer_rownum_value(sql) == 5000


def test_oracle_extract_none_when_no_limit():
    assert _extract_outer_rownum_value("SELECT a FROM t") is None


# ── 尾注释击穿硬化 (Claim 3, 与 MySQL 对称) ──
def test_oracle_extract_fetch_ignores_trailing_comment():
    # 击穿防线: FETCH 5000 后带注释, 剥注释后仍能提取 5000
    sql = "SELECT a FROM t FETCH FIRST 5000 ROWS ONLY -- c"
    assert _extract_outer_rownum_value(_normalize_sql(sql)) == 5000


def test_oracle_apply_cap_over_ceiling_with_trailing_comment_capped():
    sql, applied = _DRV._apply_cap("SELECT a FROM t FETCH FIRST 100000 ROWS ONLY -- c")
    assert applied == settings.hard_ceiling
    assert f"ROWNUM <= {settings.hard_ceiling}" in sql
    assert "100000" not in sql
    assert "--" not in sql


def test_oracle_string_literal_dashes_not_stripped():
    # tokenizer 尊重字面量: 引号内 '-- x' 不被当注释
    sql = "SELECT a, '-- x' AS c FROM t FETCH FIRST 100000 ROWS ONLY"
    out, applied = _DRV._apply_cap(sql)
    assert "'-- x'" in out
    assert applied == settings.hard_ceiling
    assert f"ROWNUM <= {settings.hard_ceiling}" in out


# ══════════════════════════════════════════════════════════════════════════════
#  OracleDriver._enforce_select_only
# ══════════════════════════════════════════════════════════════════════════════

def test_enforce_accepts_select():
    OracleDriver._enforce_select_only("SELECT id FROM t WHERE status = 1")


def test_enforce_accepts_cte():
    OracleDriver._enforce_select_only(
        "WITH cte AS (SELECT id FROM t) SELECT * FROM cte"
    )


@pytest.mark.parametrize("bad_sql", [
    "DELETE FROM t",
    "INSERT INTO t VALUES (1)",
    "UPDATE t SET x = 1",
    "DROP TABLE t",
    "CREATE TABLE t (id NUMBER)",
    "BEGIN DBMS_OUTPUT.PUT_LINE('x'); END;",
    "DECLARE v NUMBER; BEGIN v := 1; END;",
    "EXECUTE IMMEDIATE 'DROP TABLE t'",
    "SELECT 1; SELECT 2",  # 多语句
])
def test_enforce_rejects_dml_ddl(bad_sql: str):
    with pytest.raises(UnsafeQueryError):
        OracleDriver._enforce_select_only(bad_sql)


# ══════════════════════════════════════════════════════════════════════════════
#  _cursor_to_dicts — tuple rows → list[dict] 归一化
# ══════════════════════════════════════════════════════════════════════════════

class _FakeCursor:
    """最简 cursor mock: 只提供 description."""
    def __init__(self, columns: list[str]):
        self.description = [(col,) for col in columns]


def test_cursor_to_dicts_basic():
    cursor = _FakeCursor(["ID", "NAME", "STATUS"])
    rows = [(1, "Alice", "active"), (2, "Bob", "inactive")]
    result = _cursor_to_dicts(cursor, rows)
    assert result == [
        {"id": 1, "name": "Alice", "status": "active"},
        {"id": 2, "name": "Bob", "status": "inactive"},
    ]


def test_cursor_to_dicts_lowercase_keys():
    cursor = _FakeCursor(["ORDER_ID", "TOTAL_AMOUNT"])
    rows = [(100, 99.99)]
    result = _cursor_to_dicts(cursor, rows)
    assert "order_id" in result[0]
    assert "total_amount" in result[0]


def test_cursor_to_dicts_empty():
    cursor = _FakeCursor(["ID"])
    assert _cursor_to_dicts(cursor, []) == []


def test_cursor_to_dicts_no_description():
    class _NoCursor:
        description = None
    assert _cursor_to_dicts(_NoCursor(), [(1,)]) == []  # type: ignore[arg-type]


# ══════════════════════════════════════════════════════════════════════════════
#  SqlDataSourceDriver 协议: strip_outer_row_limit 公开方法
# ══════════════════════════════════════════════════════════════════════════════

def test_oracle_driver_exposes_strip_outer_row_limit():
    driver = OracleDriver()
    sql = "SELECT * FROM (SELECT id FROM t) WHERE ROWNUM <= 100"
    assert driver.strip_outer_row_limit(sql) == "SELECT id FROM t"


def test_mysql_driver_exposes_strip_outer_row_limit():
    from app.engine.drivers.mysql import MySQLDriver
    driver = MySQLDriver()
    sql = "SELECT id FROM t LIMIT 100"
    # MySQLDriver 的实现剥 LIMIT 尾部
    result = driver.strip_outer_row_limit(sql)
    assert "LIMIT 100" not in result
    assert "id FROM t" in result


# ══════════════════════════════════════════════════════════════════════════════
#  count_wrap — SqlDataSourceDriver 协议方法 (系统补 count)
# ══════════════════════════════════════════════════════════════════════════════

def test_oracle_count_wrap_produces_scalar():
    """系统补 count: 包 COUNT 返标量, 不 double-wrap (LLM 已写 COUNT 由 LLM 自负责)."""
    wrapped = _DRV.count_wrap("SELECT a FROM t")
    assert wrapped == "SELECT COUNT(*) AS cnt FROM (SELECT a FROM t) _sub"
    assert wrapped.upper().count("COUNT(*)") == 1


# ══════════════════════════════════════════════════════════════════════════════
#  C3: Thick/Thin 统一走 _run_in_executor + sync connect 的 fetch_db_profile
#
#  历史 bug: connect_async() 返回的 AsyncConnection 有 __await__，
#  不 await 则对象未连接，所有查询均报 DPY-1001: not connected to database，
#  但 connected=True 被无条件写入，导致错误凭据的数据源通过建源校验并落库。
#
#  当前实现: Thick/Thin 统一走 _run_in_executor(sync oracledb.connect),
#  测试用 _direct_executor patch 绕线程池, 直接同步调用 _fetch_db_profile_sync.
# ══════════════════════════════════════════════════════════════════════════════

async def _direct_executor(self_inner, func, *args):
    """_run_in_executor 替身: 直接同步调用, 绕开线程池 (单元测试专用)."""
    return func(*args)


def _make_fake_ds(host: str = "fake-host") -> MagicMock:
    ds = MagicMock()
    ds.id = None
    ds.host = host
    ds.port = 1521
    ds.database = "orcl"
    ds.username = "test"
    ds.password = "1"
    return ds


@pytest.mark.asyncio
async def test_thin_fetch_db_profile_connected_on_success():
    """sync connect → connected=True + 基础 profile 字段 (含 charset/nchar_charset)."""
    fake_cur = MagicMock()
    fake_cur.execute = MagicMock()
    # 5 queries: version / schema / object_count / charset / nchar_charset
    fake_cur.fetchone = MagicMock(side_effect=[
        ("Oracle Database 19c",),
        ("TEST_SCHEMA",),
        (12,),
        ("AL32UTF8",),
        ("AL16UTF16",),
    ])
    fake_cur.close = MagicMock()

    fake_conn = MagicMock()
    fake_conn.cursor = MagicMock(return_value=fake_cur)
    fake_conn.close = MagicMock()

    with patch("app.engine.drivers.oracle.oracledb.connect", return_value=fake_conn), \
         patch.object(OracleDriver, "_run_in_executor", _direct_executor):
        driver = OracleDriver()
        profile = await driver.fetch_db_profile(_make_fake_ds())

    assert profile["connected"] is True, "connect 成功 → 应连通"
    assert "profiled_at" in profile
    assert profile["version"] == "Oracle Database 19c"
    assert profile["schema"] == "TEST_SCHEMA"
    assert profile["object_count"] == 12
    assert profile["charset"] == "AL32UTF8", "NLS_CHARACTERSET 应写入 charset"
    assert profile["nchar_charset"] == "AL16UTF16", "NLS_NCHAR_CHARACTERSET 应写入 nchar_charset"
    assert fake_cur.execute.call_count == 5


@pytest.mark.asyncio
async def test_thin_fetch_db_profile_connected_false_on_error():
    """sync connect 抛异常 → connected=False，不置 True。"""
    with patch("app.engine.drivers.oracle.oracledb.connect",
               side_effect=OSError("connect refused")), \
         patch.object(OracleDriver, "_run_in_executor", _direct_executor):
        driver = OracleDriver()
        profile = await driver.fetch_db_profile(_make_fake_ds())

    assert profile["connected"] is False, "连接失败 → 不得写入 connected=True"
    assert "error" in profile
