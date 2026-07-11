"""Stage 4 Task 8 — plan_tools tests.

Mock plan_generator / plan_executor / visualizer to avoid LLM cost.
真实多步 Plan 执行已由 test_plan_executor.py 覆盖.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ════════════════════════════════════════════
#  generate_query_plan
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_generate_query_plan_passes_collections_and_calls_generator():
    """验证新契约: collections/filters/schemas 透传给 generate_plan."""
    fake_plan = MagicMock()
    fake_plan.strategy = "single_aggregate"
    fake_plan.steps = []
    fake_plan.databases = ["test_db"]
    fake_plan.to_dict = MagicMock(return_value={
        "strategy": "single_aggregate", "steps": [], "post_process": "",
    })

    with patch(
        "app.engine.tools.plan_tools.generate_plan",
        new=AsyncMock(return_value=fake_plan),
    ) as mock_gen, patch(
        "app.engine.tools.plan_tools.resolve_ds",
        new=AsyncMock(return_value=None),
    ):
        from app.engine.tools.plan_tools import generate_query_plan

        out = await generate_query_plan(
            db=MagicMock(),
            question="订单数量",
            namespace_id=1,
            collections=[{
                "db_type": "mongodb",
                "database": "test_db",
                "collection": "c_product",
            }],
            filters=[{
                "collection": "c_product",
                "field": "standard",
                "op": "regex",
                "value": "优选",
            }],
            schemas={"c_product": {"key_fields": ["_id", "standard"]}},
        )

        assert "plan" in out
        assert out["plan"]["strategy"] == "single_aggregate"
        # generate_plan(question, collections, filters, schemas, knowledge, rules)
        call_args = mock_gen.call_args
        assert call_args[0][0] == "订单数量"
        assert call_args[0][1][0]["collection"] == "c_product"
        assert call_args[0][1][0]["db_type"] == "mongodb"
        assert call_args[0][2][0]["field"] == "standard"


@pytest.mark.asyncio
async def test_generate_query_plan_trace_shape_no_keyerror():
    """回归: 用线上 trace 真实形状 (db_type/database/collection + dict schemas) 不再 KeyError.

    历史 bug: _dict_to_target 硬取 d["business_term"] → KeyError. 新契约直接透传.
    """
    fake_plan = MagicMock()
    fake_plan.to_dict = MagicMock(return_value={
        "strategy": "multi_step", "steps": [], "post_process": "",
    })
    with patch(
        "app.engine.tools.plan_tools.generate_plan",
        new=AsyncMock(return_value=fake_plan),
    ), patch(
        "app.engine.tools.plan_tools.resolve_ds",
        new=AsyncMock(return_value=None),
    ):
        from app.engine.tools.plan_tools import generate_query_plan

        out = await generate_query_plan(
            db=MagicMock(),
            question="统计品牌各资源类型数量和占比",
            namespace_id=1,
            collections=[
                {"db_type": "mongodb", "database": "rp_db", "collection": "c_brand"},
                {"db_type": "mongodb", "database": "rp_db", "collection": "c_module"},
            ],
            filters=[
                {"collection": "c_brand", "field": "brandName", "op": "regex", "value": "A 级|B 级"},
            ],
            schemas={
                "c_brand": {"key_fields": ["id", "brandName"]},
                "c_module": {"key_fields": ["docId", "groups.resources.itemType"]},
            },
        )
        assert out["plan"]["strategy"] == "multi_step"


# ════════════════════════════════════════════
#  execute_plan_tool
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_execute_plan_returns_rows_and_columns():
    """验证 dict→QueryPlan 重建 + execute_plan 调用 + 结果展平."""
    fake_result = MagicMock()
    fake_result.final = [{"_id": "x"}]
    fake_result.step_results = {1: [{"_id": "x"}]}
    fake_result.last_step_idx = 1

    with patch(
        "app.engine.tools.plan_tools.execute_plan",
        new=AsyncMock(return_value=fake_result),
    ) as mock_exec, patch(
        "app.engine.tools.plan_tools._dict_to_query_plan",
        return_value=MagicMock(),
    ):
        from app.engine.tools.plan_tools import execute_plan_tool

        out = await execute_plan_tool(
            namespace_id=1, ns_slug="test_ns",
            plan={
                "strategy": "single_aggregate",
                "steps": [],
                "post_process": "",
            },
        )
        assert "rows" in out
        assert out["rows"] == [{"_id": "x"}]
        assert out["columns"] == ["_id"]
        mock_exec.assert_awaited_once()


# ════════════════════════════════════════════
#  present_result_tool (反转自 recommend_chart_tool)
# ════════════════════════════════════════════

def test_present_result_echoes_ref_and_spec():
    from app.engine.tools.plan_tools import present_result_tool
    out = present_result_tool(
        ref="call_x",
        chart_spec={"chart_type": "line", "x": "day", "series_by": "region", "value": "amount"},
    )
    assert out["ref"] == "call_x"
    assert out["chart_spec"]["chart_type"] == "line"
    assert out["status"] == "ok"


def test_present_result_rejects_empty_ref():
    from app.engine.tools.plan_tools import present_result_tool
    out = present_result_tool(ref="", chart_spec={"chart_type": "table"})
    assert out["status"] == "error"


def test_present_result_normalizes_illegal_chart_type_to_table():
    from app.engine.tools.plan_tools import present_result_tool
    out = present_result_tool(ref="call_x", chart_spec={"chart_type": "xxx"})
    # 非法类型不报错, 归一化为 table (渲染端 fail-safe 一致)
    assert out["chart_spec"]["chart_type"] == "table"


# ════════════════════════════════════════════
#  Stage 5 — plan 末步 render mode + 补 count 截断纠正
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_execute_plan_counts_true_total_when_truncated(monkeypatch):
    """critical: 末步 SQL 疑似截断 (truncated=True) → 剥离 planner LIMIT 后 count_wrap
    拿精确总数 5; truncated = 5 > hard_ceiling(3) = True.

    execute-query-cap-contract (2026-07-11): mode 已删, plan_executor 末步靠 is_final
    + driver.count_wrap 自拼 COUNT, 不再走 mode="render"/"count" 特权.
    """
    from app.config import settings
    from app.engine.plan_executor import execute_plan
    from app.engine.plan_models import PlanStep, QueryPlan

    monkeypatch.setattr(settings, "hard_ceiling", 3)
    captured: list[str] = []

    class FakeDriver:
        def strip_outer_row_limit(self, sql: str) -> str:
            """SqlDataSourceDriver 协议方法 — 剥离末尾 LIMIT (复用 MySQLDriver 实现)."""
            from app.engine.drivers.mysql import MySQLDriver
            return MySQLDriver().strip_outer_row_limit(sql)

        def count_wrap(self, sql: str) -> str:
            from app.engine.drivers.mysql import MySQLDriver
            return MySQLDriver().count_wrap(sql)

        async def execute_query(self, ds, target, query):
            sql = (query or {}).get("sql", "")
            captured.append(sql)
            if "COUNT(*)" in sql:
                # count_wrap 产出: 断言 planner LIMIT 已剥离
                assert "LIMIT 1000" not in sql, "count 必须跑剥离 LIMIT 的查询"
                return {"rows": [{"cnt": 5}], "row_count": 1, "truncated": False}
            # 数据查询: 疑似截断 (返 3 行满载)
            return {"rows": [{"d": i} for i in range(3)], "row_count": 3, "truncated": True}

    async def fake_resolve_ds(*a, **k):
        return object()

    with patch("app.engine.drivers.get_driver", return_value=FakeDriver()), \
         patch("app.engine.tools._resolve_ds.resolve_ds", fake_resolve_ds):
        plan = QueryPlan(
            strategy="single_aggregate",
            steps=[PlanStep(
                step_idx=1, database="shop_db", collection="orders", operation="sql",
                query={"sql": "SELECT d, SUM(v) AS v FROM orders GROUP BY d LIMIT 1000"},
                pipeline=[], projection={}, sort=[], exports=[], db_type="mysql",
            )],
            post_process="", raw_llm_output="",
        )
        result = await execute_plan(plan, slug="ns", ns_id=1, sse_emit=None)

    assert result.final_truncated is True
    assert result.final_total_row_count == 5     # count 真实总数, 非 ≤1000
    assert len(result.final) == 3                # 数据查询返回的行原样
    assert any("COUNT(*)" in s for s in captured), "疑似截断必须补 count"


@pytest.mark.asyncio
async def test_execute_plan_exact_ceiling_not_truncated(monkeypatch):
    """总数恰好 == ceiling: 数据查询 truncated 疑似, count 算出 total==ceiling →
    total > ceiling 为 False → 纠正 truncated=False (假阳性纠正)."""
    from app.config import settings
    from app.engine.plan_executor import execute_plan
    from app.engine.plan_models import PlanStep, QueryPlan

    monkeypatch.setattr(settings, "hard_ceiling", 5)

    class FakeDriver:
        def strip_outer_row_limit(self, sql: str) -> str:
            from app.engine.drivers.mysql import MySQLDriver
            return MySQLDriver().strip_outer_row_limit(sql)

        def count_wrap(self, sql: str) -> str:
            from app.engine.drivers.mysql import MySQLDriver
            return MySQLDriver().count_wrap(sql)

        async def execute_query(self, ds, target, query):
            sql = (query or {}).get("sql", "")
            if "COUNT(*)" in sql:
                return {"rows": [{"cnt": 5}], "row_count": 1, "truncated": False}
            return {"rows": [{"d": i} for i in range(5)], "row_count": 5, "truncated": True}

    async def fake_resolve_ds(*a, **k):
        return object()

    with patch("app.engine.drivers.get_driver", return_value=FakeDriver()), \
         patch("app.engine.tools._resolve_ds.resolve_ds", fake_resolve_ds):
        plan = QueryPlan(
            strategy="single_aggregate",
            steps=[PlanStep(
                step_idx=1, database="d", collection="t", operation="sql",
                query={"sql": "SELECT a FROM t LIMIT 1000"},
                pipeline=[], projection={}, sort=[], exports=[], db_type="mysql",
            )],
            post_process="", raw_llm_output="",
        )
        result = await execute_plan(plan, slug="ns", ns_id=1, sse_emit=None)

    assert result.final_truncated is False   # count 纠正假阳性 (5 > 5 = False)
    assert result.final_total_row_count == 5
