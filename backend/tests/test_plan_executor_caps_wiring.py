"""Unit tests for the executor wiring of capability pre-validation (spec task 8.1).

These tests cover how `execute_plan` consumes the pure validator
(`validate_pipeline_against_caps`) wired in task 8:

- A mongodb step whose resolved-ds caps reject its pipeline is intercepted BEFORE
  any driver call: `execute_plan` raises `PlanExecutionError` and the mongo-step
  driver path (`_execute_mongo_step`) is NEVER invoked. The raised message carries
  BOTH the violation reason AND the remediation hint (R6).
- A native/empty-caps mongodb step passes through to the driver (validation does
  not block; `_execute_mongo_step` is reached).
- A mysql step is not pre-validated at all (caps resolution is skipped) and passes
  through to the driver (`_execute_sql_step` is reached).

`_resolve_step_caps` (DB I/O) is patched to return chosen caps; `_execute_mongo_step`
/ `_execute_sql_step` are patched with mocks to detect whether the driver path was
reached. No live DocumentDB / database required.

Validates: Requirements 5.1, 5.3, 5.5
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.engine.plan_executor import PlanExecutionError, execute_plan
from app.engine.plan_models import PlanStep, QueryPlan


def _step(idx=1, db="db1", coll="c", op="aggregate",
          pipeline=None, query=None, exports=None, db_type="mongodb") -> PlanStep:
    return PlanStep(
        step_idx=idx, database=db, collection=coll, operation=op,
        pipeline=pipeline or [], query=query or {},
        exports=exports or [], db_type=db_type,
    )


def _documentdb_caps() -> dict:
    """DocumentDB-profile subset carrying real restrictions (from aws_documentdb.json)."""
    return {
        "version": "5.0.0",
        "flavor": "documentdb",
        "unsupported_ops": ["$round", "$function"],
        "unsupported_stage_variants": ["$facet", "$lookup.let_pipeline"],
        "syntax_constraints": ["project_no_dollar_fieldpath"],
        "equivalent_hints": [
            {"restriction": "project_no_dollar_fieldpath",
             "suggestion": "字段引用用裸名, 不要嵌入 $"},
        ],
        "agg_ops_unsupported": ["$round", "$function"],
    }


def _native_caps() -> dict:
    """Native MongoDB: all-empty restriction lists → no restrictions."""
    return {
        "version": "5.0.0",
        "flavor": "mongodb",
        "unsupported_ops": [],
        "unsupported_stage_variants": [],
        "syntax_constraints": [],
        "equivalent_hints": [],
        "agg_ops_unsupported": [],
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Violation → raise before any driver call, message carries reason + hint (R6)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_violation_raises_before_driver_call_and_message_carries_reason_and_hint():
    """A mongodb step violating its resolved caps is rejected BEFORE the driver.

    - execute_plan raises PlanExecutionError with the step_idx.
    - _execute_mongo_step (the driver path) is NEVER invoked.
    - the raised message includes BOTH the reason AND the remediation hint (R6).
    """
    # $project with embedded-$ fieldpath → violates project_no_dollar_fieldpath (16410).
    plan = QueryPlan(
        strategy="single_aggregate",
        steps=[_step(idx=1, op="aggregate", pipeline=[{"$project": {"x": "$a.$id_str"}}])],
    )

    mongo_driver_path = AsyncMock(return_value=[{"_id": "a"}])
    with patch("app.engine.plan_executor._resolve_step_caps",
               new=AsyncMock(return_value=_documentdb_caps())), \
         patch("app.engine.plan_executor._execute_mongo_step", new=mongo_driver_path):
        with pytest.raises(PlanExecutionError) as ei:
            await execute_plan(plan, slug="ns", ns_id=1)

    # raised before any driver dispatch
    mongo_driver_path.assert_not_called()

    err = ei.value
    assert err.step_idx == 1
    # the cause is the code-less RuntimeError carrying the R6 message
    msg = str(err.cause)
    # reason fragment (restriction named + intercepted-before-execution)
    assert "project_no_dollar_fieldpath" in msg
    assert "执行前被拦截" in msg
    # remediation hint present, after a "建议:" marker (R6: both reason AND suggestion)
    assert "建议:" in msg
    assert "字段引用用裸名, 不要嵌入 $" in msg
    # the wrapped PlanExecutionError str also surfaces the message
    assert "建议:" in str(err)


@pytest.mark.asyncio
async def test_unsupported_op_violation_message_carries_generic_hint_when_unconfigured():
    """An unsupported op without a configured hint still surfaces a remediation (generic fallback)."""
    caps = _documentdb_caps()
    caps["equivalent_hints"] = []  # no hint configured for $round
    plan = QueryPlan(
        strategy="single_aggregate",
        steps=[_step(idx=1, op="aggregate",
                     pipeline=[{"$project": {"r": {"$round": ["$amount", 2]}}}])],
    )
    mongo_driver_path = AsyncMock(return_value=[{"_id": "a"}])
    with patch("app.engine.plan_executor._resolve_step_caps",
               new=AsyncMock(return_value=caps)), \
         patch("app.engine.plan_executor._execute_mongo_step", new=mongo_driver_path):
        with pytest.raises(PlanExecutionError) as ei:
            await execute_plan(plan, slug="ns", ns_id=1)

    mongo_driver_path.assert_not_called()
    msg = str(ei.value.cause)
    assert "$round" in msg
    assert "建议:" in msg  # GENERIC_RESTRICTION_HINT concatenated after the marker


# ══════════════════════════════════════════════════════════════════════════════
#  Passthrough: native/empty caps mongodb step reaches the driver
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_native_caps_mongodb_step_passes_through_to_driver():
    """A clean step under native (empty) caps is not blocked: _execute_mongo_step is reached."""
    plan = QueryPlan(
        strategy="single_aggregate",
        steps=[_step(idx=1, op="aggregate", pipeline=[{"$match": {"status": "active"}}])],
    )
    mongo_driver_path = AsyncMock(return_value=([{"_id": "a"}], False, 1))
    with patch("app.engine.plan_executor._resolve_step_caps",
               new=AsyncMock(return_value=_native_caps())), \
         patch("app.engine.plan_executor._execute_mongo_step", new=mongo_driver_path):
        result = await execute_plan(plan, slug="ns", ns_id=1)

    mongo_driver_path.assert_awaited_once()
    assert result.final == [{"_id": "a"}]


@pytest.mark.asyncio
async def test_caps_none_mongodb_step_passes_through_to_driver():
    """When caps resolve to None (no datasource / probe failed), the step is not blocked."""
    plan = QueryPlan(
        strategy="single_aggregate",
        steps=[_step(idx=1, op="aggregate", pipeline=[{"$project": {"x": "$a.$id_str"}}])],
    )
    mongo_driver_path = AsyncMock(return_value=([{"_id": "a"}], False, 1))
    with patch("app.engine.plan_executor._resolve_step_caps",
               new=AsyncMock(return_value=None)), \
         patch("app.engine.plan_executor._execute_mongo_step", new=mongo_driver_path):
        result = await execute_plan(plan, slug="ns", ns_id=1)

    mongo_driver_path.assert_awaited_once()
    assert result.final == [{"_id": "a"}]


# ══════════════════════════════════════════════════════════════════════════════
#  Passthrough: mysql step is not pre-validated, reaches the driver
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_mysql_step_skips_pre_validation_and_passes_through_to_driver():
    """A mysql step is never pre-validated (caps resolution skipped) and reaches the driver."""
    plan = QueryPlan(
        strategy="single_aggregate",
        steps=[_step(idx=1, db="dbA", db_type="mysql", op="sql",
                     query={"sql": "SELECT COUNT(*) AS n FROM t LIMIT 100"})],
    )
    resolve_caps = AsyncMock(return_value=_documentdb_caps())
    sql_driver_path = AsyncMock(return_value=([{"n": 5}], False, 1))
    with patch("app.engine.plan_executor._resolve_step_caps", new=resolve_caps), \
         patch("app.engine.plan_executor._execute_sql_step", new=sql_driver_path):
        result = await execute_plan(plan, slug="ns", ns_id=1)

    # mysql step → pre-validation does not apply: caps are never resolved
    resolve_caps.assert_not_called()
    sql_driver_path.assert_awaited_once()
    assert result.final == [{"n": 5}]
