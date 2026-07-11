"""行保护剥离 helper — 外层 LIMIT 剥离 (SQL) / 尾部行 stage 剥离 (mongo).

execute-query-cap-contract (2026-07-11): render mode 已删, 末步 LIMIT 由 planner
自表达 (≤ceiling 尊重), 不再有 `_wrap_by_mode(..., "render", ...)` 特权覆盖.
本文件保留 strip helper 测 — 它们供 plan_executor 补 count 时剥外层行保护用
(count_wrap 前先 strip, 否则子查询被 planner LIMIT 封顶).
"""
from __future__ import annotations

from app.engine.drivers.mongo import _strip_tail_row_stages
from app.engine.drivers.mysql import MySQLDriver


def test_mysql_strip_outer_limit_leaves_subquery_limit():
    # 子查询内 LIMIT 不应被剥离, 仅剥外层 (strip_outer_row_limit 是 SqlDataSourceDriver 协议方法)
    sql = "SELECT * FROM (SELECT a FROM t LIMIT 5) _s LIMIT 1000"
    stripped = MySQLDriver().strip_outer_row_limit(sql)
    assert stripped.endswith("_s")
    assert "LIMIT 5" in stripped


def test_mongo_strip_tail_row_stages_removes_trailing_limit():
    pipeline = [{"$match": {"x": 1}}, {"$group": {"_id": "$d"}}, {"$limit": 1000}]
    out = _strip_tail_row_stages(pipeline)
    assert out == [{"$match": {"x": 1}}, {"$group": {"_id": "$d"}}]


def test_mongo_strip_tail_row_stages_removes_consecutive_tail():
    pipeline = [{"$match": {"x": 1}}, {"$skip": 10}, {"$limit": 1000}]
    out = _strip_tail_row_stages(pipeline)
    assert out == [{"$match": {"x": 1}}]


def test_mongo_strip_tail_row_stages_keeps_middle_stages():
    # 中间的 $limit 不剥 (只剥尾部连续行 stage)
    pipeline = [{"$limit": 100}, {"$group": {"_id": "$d"}}]
    out = _strip_tail_row_stages(pipeline)
    assert out == [{"$limit": 100}, {"$group": {"_id": "$d"}}]
