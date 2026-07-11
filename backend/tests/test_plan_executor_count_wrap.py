"""plan_executor 末步截断补 count 走 count_wrap (非 driver count mode)."""
from app.engine.drivers.mysql import MySQLDriver


def test_count_wrap_produces_scalar_count():
    """补 count: strip_outer_row_limit + count_wrap → SELECT COUNT(*) AS cnt FROM (..) _sub."""
    drv = MySQLDriver()
    stripped = drv.strip_outer_row_limit("SELECT * FROM t WHERE x=1 LIMIT 1000")
    wrapped = drv.count_wrap(stripped)
    assert wrapped == "SELECT COUNT(*) AS cnt FROM (SELECT * FROM t WHERE x=1) _sub"
    # 关键: 不再 double-wrap (旧 mode=count 会再包一层 → 恒 1)
    assert wrapped.count("COUNT") == 1


def test_count_wrap_on_count_query_no_double():
    """即使原 SQL 是 COUNT, 补 count 路径也只包一层 (count_wrap 不检测, 但 plan_executor
    只对【数据查询末步截断】补 count, 不会对 COUNT 查询补). 此测验 count_wrap 单层语义."""
    drv = MySQLDriver()
    stripped = drv.strip_outer_row_limit("SELECT COUNT(*) FROM t WHERE x=1")
    wrapped = drv.count_wrap(stripped)
    # 单层 wrap (count_wrap 本身不防 double, 调用方责任 — plan_executor 仅对数据查询调)
    assert wrapped.count("SELECT COUNT(*) AS cnt") == 1
