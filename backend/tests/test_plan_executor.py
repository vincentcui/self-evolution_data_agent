"""
PlanExecutor 测试 — 变量校验 / 变量替换 / exports 抽取 / 跨库执行.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.engine.plan_executor import (
    PlanExecutionError,
    VariableResolutionError,
    _extract_exports,
    _render_sql_vars,
    _resolve_vars,
    _sql_literal,
    execute_plan,
    pre_validate_vars,
)
from app.engine.plan_models import PlanStep, QueryPlan


def _step(idx=1, db="db1", coll="c", op="find",
          pipeline=None, query=None, exports=None, db_type="mongodb") -> PlanStep:
    return PlanStep(
        step_idx=idx, database=db, collection=coll, operation=op,
        pipeline=pipeline or [], query=query or {},
        exports=exports or [], db_type=db_type,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  pre_validate_vars
# ══════════════════════════════════════════════════════════════════════════════

class TestPreValidateVars:
    def test_no_vars_ok(self):
        plan = QueryPlan(strategy="single_aggregate", steps=[_step()])
        pre_validate_vars(plan)  # 不抛

    def test_valid_cross_step_ref(self):
        s1 = _step(idx=1, exports=["ids"])
        s2 = _step(idx=2, query={"x": "{{step1.ids}}"})
        plan = QueryPlan(strategy="multi_step", steps=[s1, s2])
        pre_validate_vars(plan)

    def test_forward_ref_rejected(self):
        s1 = _step(idx=1, query={"x": "{{step2.ids}}"})
        s2 = _step(idx=2, exports=["ids"])
        plan = QueryPlan(strategy="multi_step", steps=[s1, s2])
        with pytest.raises(VariableResolutionError):
            pre_validate_vars(plan)

    def test_unknown_step_rejected(self):
        s1 = _step(idx=1, exports=["ids"])
        s2 = _step(idx=2, query={"x": "{{step99.ids}}"})
        plan = QueryPlan(strategy="multi_step", steps=[s1, s2])
        with pytest.raises(VariableResolutionError):
            pre_validate_vars(plan)

    def test_var_not_in_exports_rejected(self):
        s1 = _step(idx=1, exports=["ids"])
        s2 = _step(idx=2, query={"x": "{{step1.bogus}}"})
        plan = QueryPlan(strategy="multi_step", steps=[s1, s2])
        with pytest.raises(VariableResolutionError):
            pre_validate_vars(plan)

    def test_partial_embedding_ignored(self):
        """部分嵌入 (prefix{{stepN.var}}suffix) 不抛错, 只给 WARN."""
        s1 = _step(idx=1, exports=["ids"])
        s2 = _step(idx=2, query={"x": "prefix-{{step1.ids}}-suffix"})
        plan = QueryPlan(strategy="multi_step", steps=[s1, s2])
        pre_validate_vars(plan)  # 不抛


# ══════════════════════════════════════════════════════════════════════════════
#  _resolve_vars
# ══════════════════════════════════════════════════════════════════════════════

class TestResolveVars:
    def test_replace_string(self):
        prev = {1: {"ids": ["a", "b"]}}
        assert _resolve_vars("{{step1.ids}}", prev) == ["a", "b"]

    def test_nested_dict(self):
        prev = {1: {"ids": [1, 2]}}
        out = _resolve_vars({"q": {"$in": "{{step1.ids}}"}}, prev)
        assert out == {"q": {"$in": [1, 2]}}

    def test_list_elements(self):
        prev = {1: {"x": 42}}
        assert _resolve_vars(["a", "{{step1.x}}", "b"], prev) == ["a", 42, "b"]

    def test_plain_string_unchanged(self):
        assert _resolve_vars("no var here", {}) == "no var here"

    def test_missing_step_raises(self):
        with pytest.raises(VariableResolutionError):
            _resolve_vars("{{step5.x}}", {1: {"x": 1}})

    def test_missing_var_raises(self):
        with pytest.raises(VariableResolutionError):
            _resolve_vars("{{step1.bogus}}", {1: {"x": 1}})


# ══════════════════════════════════════════════════════════════════════════════
#  _extract_exports
# ══════════════════════════════════════════════════════════════════════════════

class TestExtractExports:
    def test_scalar_fields(self):
        docs = [{"id": "a", "n": 1}, {"id": "b", "n": 2}]
        out = _extract_exports(docs, ["id"])
        assert out == {"id": ["a", "b"]}

    def test_list_fields_extended(self):
        """$push 结果通常是 list, 应 extend 而非 append."""
        docs = [{"allIds": ["x", "y", "z"]}]
        out = _extract_exports(docs, ["allIds"])
        assert out == {"allIds": ["x", "y", "z"]}

    def test_none_skipped(self):
        docs = [{"id": None}, {"id": "a"}]
        out = _extract_exports(docs, ["id"])
        assert out == {"id": ["a"]}

    def test_missing_exports_empty(self):
        docs = [{"x": 1}]
        out = _extract_exports(docs, ["y"])
        assert out == {"y": []}

    def test_skips_non_dict(self):
        docs = [{"id": "a"}, "not a dict", 42]
        out = _extract_exports(docs, ["id"])
        assert out == {"id": ["a"]}


# ══════════════════════════════════════════════════════════════════════════════
#  execute_plan — end-to-end with mocked driver
# ══════════════════════════════════════════════════════════════════════════════

def _mock_ds():
    ds = MagicMock()
    ds.id = 1
    ds.database = "db1"
    # db_profile_json 必须是真值 (None/JSON 串) — 否则 _resolve_step_caps 的
    # json.loads(ds.db_profile_json or "{}") 收到 MagicMock 抛 TypeError.
    # None → "{}" → caps 解析 fail-safe 返 None (pre-validation 不阻断执行).
    ds.db_profile_json = None
    return ds


def _mock_driver(return_value=None):
    driver = MagicMock()
    driver.execute_query = AsyncMock(return_value={
        "rows": return_value if return_value is not None else [{"_id": "a"}],
        "row_count": len(return_value) if return_value is not None else 1,
        "truncated": False,
        "elapsed_ms": 10,
    })
    return driver


@pytest.mark.asyncio
async def test_execute_plan_single_step():
    driver = _mock_driver()
    plan = QueryPlan(
        strategy="single_aggregate",
        steps=[_step(idx=1, op="aggregate", pipeline=[{"$match": {}}])],
    )
    with patch("app.engine.tools._resolve_ds.resolve_ds", new=AsyncMock(return_value=_mock_ds())), \
         patch("app.engine.drivers.get_driver", return_value=driver):
        result = await execute_plan(plan, slug="ns", ns_id=1)
    assert result.final == [{"_id": "a"}]
    driver.execute_query.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_plan_multi_step_cross_db_var_substitution():
    """两步分别在不同 database, step2 引用 step1 exports."""
    driver = MagicMock()
    call_count = [0]

    async def _execute_query(ds, target, query, mode="single", **kw):
        call_count[0] += 1
        if call_count[0] == 1:
            return {"rows": [{"allIds": ["x", "y"]}], "row_count": 1,
                    "truncated": False, "elapsed_ms": 5}
        return {"rows": [{"n": 1}, {"n": 2}], "row_count": 2,
                "truncated": False, "elapsed_ms": 5}

    driver.execute_query = AsyncMock(side_effect=_execute_query)

    step1 = _step(idx=1, db="dbA", op="aggregate",
                  pipeline=[{"$group": {"_id": None, "allIds": {"$push": "$_id"}}}],
                  exports=["allIds"])
    step2 = _step(idx=2, db="dbB", op="aggregate",
                  pipeline=[{"$match": {"ref": {"$in": "{{step1.allIds}}"}}}])
    plan = QueryPlan(strategy="multi_step", steps=[step1, step2])

    with patch("app.engine.tools._resolve_ds.resolve_ds", new=AsyncMock(return_value=_mock_ds())), \
         patch("app.engine.drivers.get_driver", return_value=driver):
        result = await execute_plan(plan, slug="ns", ns_id=1)

    # 验证 step2 收到的 pipeline 已将变量替换为实际列表
    step2_call = driver.execute_query.await_args_list[1]
    query_payload = step2_call.args[2]  # third positional arg is query dict
    pipeline = query_payload["pipeline"]
    assert pipeline[0]["$match"]["ref"]["$in"] == ["x", "y"]
    assert len(result.final) == 2


@pytest.mark.asyncio
async def test_execute_plan_engine_not_found_raises():
    plan = QueryPlan(strategy="single_aggregate",
                     steps=[_step(idx=1, db="missing")])
    with patch("app.engine.tools._resolve_ds.resolve_ds", new=AsyncMock(return_value=None)):
        with pytest.raises(PlanExecutionError) as ei:
            await execute_plan(plan, slug="ns", ns_id=1)
    assert ei.value.step_idx == 1


@pytest.mark.asyncio
async def test_execute_plan_engine_raises_wrapped():
    driver = MagicMock()
    driver.execute_query = AsyncMock(side_effect=RuntimeError("mongo fail"))
    plan = QueryPlan(strategy="single_aggregate", steps=[_step(idx=1, op="aggregate", pipeline=[{"$match": {}}])])
    with patch("app.engine.tools._resolve_ds.resolve_ds", new=AsyncMock(return_value=_mock_ds())), \
         patch("app.engine.drivers.get_driver", return_value=driver):
        with pytest.raises(PlanExecutionError) as ei:
            await execute_plan(plan, slug="ns", ns_id=1)
    assert ei.value.step_idx == 1
    assert "mongo fail" in str(ei.value.cause)


@pytest.mark.asyncio
async def test_execute_plan_pre_validate_rejects_forward_ref():
    s1 = _step(idx=1, query={"x": "{{step2.y}}"})
    s2 = _step(idx=2, exports=["y"])
    plan = QueryPlan(strategy="multi_step", steps=[s1, s2])
    with pytest.raises(VariableResolutionError):
        await execute_plan(plan, slug="ns", ns_id=1)


# ══════════════════════════════════════════════════════════════════════════════
#  _sql_literal / _render_sql_vars — 跨引擎 SQL 下游变量注入 (Plan A Part 2)
# ══════════════════════════════════════════════════════════════════════════════

class TestSqlLiteral:
    def test_none_to_null(self):
        assert _sql_literal(None) == "NULL"

    def test_bool(self):
        assert _sql_literal(True) == "TRUE"
        assert _sql_literal(False) == "FALSE"

    def test_int_float_bare(self):
        assert _sql_literal(42) == "42"
        assert _sql_literal(3.14) == "3.14"

    def test_string_quoted(self):
        assert _sql_literal("abc") == "'abc'"

    def test_string_single_quote_escaped(self):
        # 注入防护: 单引号转义为两个单引号
        assert _sql_literal("a'b") == "'a''b'"

    def test_string_backslash_escaped(self):
        assert _sql_literal("a\\b") == "'a\\\\b'"


class TestRenderSqlVars:
    def test_list_rendered_as_comma_list(self):
        prev = {1: {"ids": [1, 2, 3]}}
        sql = "SELECT * FROM t WHERE id IN ({{step1.ids}})"
        out = _render_sql_vars(sql, prev)
        assert out == "SELECT * FROM t WHERE id IN (1, 2, 3)"

    def test_string_list_escaped(self):
        prev = {1: {"codes": ["A", "B'C"]}}
        sql = "SELECT * FROM t WHERE code IN ({{step1.codes}})"
        out = _render_sql_vars(sql, prev)
        assert out == "SELECT * FROM t WHERE code IN ('A', 'B''C')"

    def test_scalar_value(self):
        prev = {1: {"max_id": 99}}
        sql = "SELECT * FROM t WHERE id < {{step1.max_id}}"
        out = _render_sql_vars(sql, prev)
        assert out == "SELECT * FROM t WHERE id < 99"

    def test_empty_list_to_null(self):
        prev = {1: {"ids": []}}
        sql = "SELECT * FROM t WHERE id IN ({{step1.ids}})"
        out = _render_sql_vars(sql, prev)
        assert out == "SELECT * FROM t WHERE id IN (NULL)"

    def test_missing_step_raises(self):
        with pytest.raises(VariableResolutionError):
            _render_sql_vars("... IN ({{step9.x}})", {1: {"x": [1]}})

    def test_no_vars_unchanged(self):
        sql = "SELECT * FROM t WHERE id = 1"
        assert _render_sql_vars(sql, {}) == sql


class TestPreValidateVarsSqlEmbedded:
    def test_sql_embedded_ref_validated(self):
        """mysql step 的嵌入式变量引用应被校验 (而非仅 WARN)."""
        s1 = _step(idx=1, db_type="mongodb", op="aggregate",
                   pipeline=[{"$group": {"_id": None, "ids": {"$push": "$_id"}}}],
                   exports=["ids"])
        s2 = _step(idx=2, db_type="mysql", op="sql",
                   query={"sql": "SELECT * FROM t WHERE id IN ({{step1.ids}})"})
        plan = QueryPlan(strategy="multi_step", steps=[s1, s2])
        pre_validate_vars(plan)  # 合法, 不抛

    def test_sql_embedded_bad_var_rejected(self):
        s1 = _step(idx=1, db_type="mongodb", exports=["ids"])
        s2 = _step(idx=2, db_type="mysql", op="sql",
                   query={"sql": "SELECT * FROM t WHERE id IN ({{step1.bogus}})"})
        plan = QueryPlan(strategy="multi_step", steps=[s1, s2])
        with pytest.raises(VariableResolutionError):
            pre_validate_vars(plan)

    def test_sql_embedded_forward_ref_rejected(self):
        s1 = _step(idx=1, db_type="mysql", op="sql",
                   query={"sql": "SELECT * FROM t WHERE id IN ({{step2.ids}})"})
        s2 = _step(idx=2, db_type="mongodb", exports=["ids"])
        plan = QueryPlan(strategy="multi_step", steps=[s1, s2])
        with pytest.raises(VariableResolutionError):
            pre_validate_vars(plan)


@pytest.mark.asyncio
async def test_execute_plan_oracle_step_enters_sql_driver():
    """Oracle SQL step 应走 _execute_sql_step 路径 (不落入 MongoDB 分支)."""
    driver = _mock_driver([{"order_id": 1, "total": 100.0}])
    # strip_outer_row_limit 是 SqlDataSourceDriver 协议方法
    driver.strip_outer_row_limit = lambda sql: sql

    plan = QueryPlan(
        strategy="single_aggregate",
        steps=[_step(idx=1, db="sales_db", db_type="oracle", op="sql",
                     query={"sql": "SELECT ORDER_ID, TOTAL FROM ORDERS WHERE ROWNUM <= 100"})],
    )
    with patch("app.engine.tools._resolve_ds.resolve_ds", new=AsyncMock(return_value=_mock_ds())), \
         patch("app.engine.drivers.get_driver", return_value=driver):
        result = await execute_plan(plan, slug="ns", ns_id=1)

    driver.execute_query.assert_awaited_once()
    assert result.final == [{"order_id": 1, "total": 100.0}]


@pytest.mark.asyncio
async def test_execute_plan_mongo_to_oracle_var_injection():
    """Mongo→Oracle: step1 导出 ids, step2 Oracle SQL 的 IN 列表注入实际值."""
    call_count = [0]
    captured = {}

    async def _execute_query(ds, target, query, mode="single", **kw):
        call_count[0] += 1
        if call_count[0] == 1:
            return {"rows": [{"cust_id": 10}, {"cust_id": 20}], "row_count": 2,
                    "truncated": False, "elapsed_ms": 5}
        captured["sql"] = query["sql"]
        return {"rows": [{"total": 999.0}], "row_count": 1, "truncated": False, "elapsed_ms": 5}

    driver = MagicMock()
    driver.execute_query = AsyncMock(side_effect=_execute_query)
    driver.strip_outer_row_limit = lambda sql: sql  # SqlDataSourceDriver 协议方法

    step1 = _step(idx=1, db="catalog_db", db_type="mongodb", op="aggregate",
                  pipeline=[{"$project": {"cust_id": 1}}], exports=["cust_id"])
    step2 = _step(
        idx=2,
        db="sales_db",
        db_type="oracle",
        op="sql",
        query={
            "sql": (
                "SELECT SUM(AMOUNT) AS total FROM ORDERS "
                "WHERE CUST_ID IN ({{step1.cust_id}})"
            )
        },
    )
    plan = QueryPlan(strategy="multi_step", steps=[step1, step2])

    with patch("app.engine.tools._resolve_ds.resolve_ds", new=AsyncMock(return_value=_mock_ds())), \
         patch("app.engine.drivers.get_driver", return_value=driver):
        result = await execute_plan(plan, slug="ns", ns_id=1)

    assert "IN (10, 20)" in captured["sql"]
    assert result.final == [{"total": 999.0}]


@pytest.mark.asyncio
async def test_execute_plan_unknown_db_type_raises_not_mongo():
    """未知 db_type 应明确抛 UnsupportedDataSourceTypeError, 不落入 MongoDB 分支."""
    from app.engine.drivers._exceptions import UnsupportedDataSourceTypeError

    plan = QueryPlan(
        strategy="single_aggregate",
        steps=[_step(idx=1, db_type="postgresql", op="sql",
                     query={"sql": "SELECT 1"})],
    )
    with patch("app.engine.tools._resolve_ds.resolve_ds", new=AsyncMock(return_value=_mock_ds())), \
         patch(
             "app.engine.drivers.get_driver",
             side_effect=UnsupportedDataSourceTypeError("postgresql"),
         ):
        with pytest.raises(PlanExecutionError) as ei:
            await execute_plan(plan, slug="ns", ns_id=1)
    # PlanExecutionError 包裹了 UnsupportedDataSourceTypeError
    assert isinstance(ei.value.cause, UnsupportedDataSourceTypeError)


@pytest.mark.asyncio
async def test_execute_plan_mysql_downstream_var_injection():
    """Mongo→MySQL: step1 导出 ids, step2 SQL 的 IN 列表注入实际值."""
    driver = MagicMock()
    call_count = [0]
    captured = {}

    async def _execute_query(ds, target, query, mode="single", **kw):
        call_count[0] += 1
        if call_count[0] == 1:
            return {"rows": [{"pid": 10}, {"pid": 20}], "row_count": 2,
                    "truncated": False, "elapsed_ms": 5}
        captured["sql"] = query["sql"]
        return {"rows": [{"n": 5}], "row_count": 1, "truncated": False, "elapsed_ms": 5}

    driver.execute_query = AsyncMock(side_effect=_execute_query)

    step1 = _step(idx=1, db="dbA", db_type="mongodb", op="aggregate",
                  pipeline=[{"$project": {"pid": 1}}], exports=["pid"])
    step2 = _step(idx=2, db="dbB", db_type="mysql", op="sql",
                  query={"sql": "SELECT COUNT(*) AS n FROM sales WHERE product_id IN ({{step1.pid}}) LIMIT 100"})
    plan = QueryPlan(strategy="multi_step", steps=[step1, step2])

    with patch("app.engine.tools._resolve_ds.resolve_ds", new=AsyncMock(return_value=_mock_ds())), \
         patch("app.engine.drivers.get_driver", return_value=driver):
        result = await execute_plan(plan, slug="ns", ns_id=1)

    assert "IN (10, 20)" in captured["sql"]
    assert len(result.final) == 1


# ══════════════════════════════════════════════════════════════════════════════
#  PlanStep.limit write-only 字段删除 (Task 5)
# ══════════════════════════════════════════════════════════════════════════════

def test_plan_step_has_no_limit_field():
    """step.limit write-only 字段已删 — 执行路径从不读它做控制流, 仅曾进 log/trace 误导."""
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(PlanStep)}
    assert "limit" not in field_names


def test_plan_step_to_dict_omits_limit():
    step = PlanStep(step_idx=1, database="db", collection="orders", operation="find")
    assert "limit" not in step.to_dict()


def test_plan_step_construction_rejects_limit_kwarg():
    """破坏性契约: limit kwarg 已移除, 传入应 TypeError (防止 callsite 漏迁移回潜)."""
    with pytest.raises(TypeError):
        PlanStep(step_idx=1, database="db", collection="c", operation="find", limit=100)  # type: ignore[call-arg]
