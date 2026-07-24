"""Stage 4 Task 6 — cost-aware tools 测试 (estimate_query_cost).

Mongo 驱动用 unittest.mock.AsyncMock + MagicMock (CI 真 mongo 代价高,
与 Task 4 / 5 同模式).

P0-3 Task 4 update: estimate_query_cost 签名加 sse_emit, 所有调用点同步补齐.
datasource_id 已废弃 → namespace_id + database 双参.

历史: count 短路 / 分批 aggregate 死函数已删 (Stage 3 后被 execute_query
取代), 对应用例同步清除。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ════════════════════════════════════════════
#  estimate_query_cost
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_estimate_query_cost_returns_docs_and_indexes():
    fake_explain = {
        "executionStats": {"totalDocsExamined": 1234},
        "queryPlanner": {
            "winningPlan": {"inputStage": {"indexName": "categoryId_1"}},
        },
    }
    fake_find = MagicMock()
    fake_find.explain = AsyncMock(return_value=fake_explain)
    fake_coll = MagicMock()
    fake_coll.find = MagicMock(return_value=fake_find)
    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_coll

    with patch(
        "app.engine.tools.cost_tools.get_mongo_db",
        new=AsyncMock(return_value=fake_db),
    ):
        from app.engine.tools.cost_tools import estimate_query_cost

        out = await estimate_query_cost(
            namespace_id=1, collection="c_product", database="testdb",
            filter={"categoryId": {"$in": ["b1"]}},
            sse_emit=AsyncMock(),
        )

    assert out["estimated_docs"] == 1234
    assert "categoryId_1" in out["hit_indexes"]
    assert out.get("warning") is None


@pytest.mark.asyncio
async def test_estimate_query_cost_warns_when_above_limit(monkeypatch):
    monkeypatch.setattr(
        "app.config.settings.query_cost_single_layer_limit", 100
    )
    fake_explain = {
        "executionStats": {"totalDocsExamined": 200},
        "queryPlanner": {"winningPlan": {}},
    }
    fake_find = MagicMock()
    fake_find.explain = AsyncMock(return_value=fake_explain)
    fake_coll = MagicMock()
    fake_coll.find = MagicMock(return_value=fake_find)
    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_coll

    fake_emit = AsyncMock()
    with patch(
        "app.engine.tools.cost_tools.get_mongo_db",
        new=AsyncMock(return_value=fake_db),
    ):
        from app.engine.tools.cost_tools import estimate_query_cost

        out = await estimate_query_cost(
            namespace_id=1, collection="c_product", database="testdb",
            filter={},
            sse_emit=fake_emit,
        )

    assert out["warning"] is not None
    assert "overflow" in out["warning"]
    # emit 应该被调用 (超阈)
    fake_emit.assert_awaited()


@pytest.mark.asyncio
async def test_estimate_query_cost_advice_no_dead_tools(monkeypatch):
    """single_layer_overflow 时 cost_warning advice 不得引用已删工具名.

    Phase 8 T22: count 短路 / 分批 aggregate 工具已退役,
    advice 文本若仍提及死工具名会误导 LLM 调不存在的工具。
    """
    monkeypatch.setattr(
        "app.config.settings.query_cost_single_layer_limit", 100
    )
    fake_explain = {
        "executionStats": {"totalDocsExamined": 200},
        "queryPlanner": {"winningPlan": {}},
    }
    fake_find = MagicMock()
    fake_find.explain = AsyncMock(return_value=fake_explain)
    fake_coll = MagicMock()
    fake_coll.find = MagicMock(return_value=fake_find)
    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_coll

    fake_emit = AsyncMock()
    with patch(
        "app.engine.tools.cost_tools.get_mongo_db",
        new=AsyncMock(return_value=fake_db),
    ):
        from app.engine.tools.cost_tools import estimate_query_cost

        await estimate_query_cost(
            namespace_id=1, collection="c", database="d", filter={},
            sse_emit=fake_emit,
        )

    fake_emit.assert_awaited()
    payload = fake_emit.await_args.args[0]
    advice = payload["data"]["advice"]
    assert "execute_count_only" not in advice
    assert "execute_batched_aggregate" not in advice
    assert "execute_query" in advice


def test_cost_tools_module_has_no_dead_functions():
    """Phase 8 T22: 死函数定义不得残留."""
    import app.engine.tools.cost_tools as ct

    assert not hasattr(ct, "execute_count_only")
    assert not hasattr(ct, "execute_batched_aggregate")
    assert not hasattr(ct, "_substitute_batch")


# ════════════════════════════════════════════
#  Stage 4 Task 6 follow-up — 边界 / 兜底测试
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_estimate_query_cost_aggregate_returns_pruned_explain():
    """pipeline_stages 非空 → aggregate explain 路径, 返 mongo_version + pruned explain_raw + hint."""
    fake_explain = {
        "stages": [
            {"$cursor": {"queryPlanner": {"winningPlan": {"stage": "COLLSCAN"}},
                          "executionStats": {"totalDocsExamined": 9999}}},
            {"$group": {"nReturned": 5, "executionTimeMillisEstimate": 12}},
        ],
        "$clusterTime": {"clusterTime": "should_be_pruned"},
        "operationTime": "should_be_pruned",
        "serverInfo": {"host": "should_be_pruned"},
        "ok": 1,
    }
    fake_client = MagicMock()
    fake_client.server_info = AsyncMock(return_value={"version": "6.0.13"})
    fake_db = MagicMock()
    fake_db.client = fake_client
    fake_db.command = AsyncMock(return_value=fake_explain)

    with patch(
        "app.engine.tools.cost_tools.get_mongo_db",
        new=AsyncMock(return_value=fake_db),
    ):
        from app.engine.tools.cost_tools import estimate_query_cost
        out = await estimate_query_cost(
            namespace_id=1, collection="c_product", database="testdb",
            filter={"status": "paid"},
            sse_emit=AsyncMock(),
            pipeline_stages=[{"$group": {"_id": "$categoryId"}}],
        )

    assert out["mongo_version"] == "6.0.13"
    assert "explain_raw" in out
    assert "hint" in out
    raw = out["explain_raw"]
    assert "$clusterTime" not in raw
    assert "operationTime" not in raw
    assert "serverInfo" not in raw
    assert raw["stages"][0]["$cursor"]["executionStats"]["totalDocsExamined"] == 9999
    assert raw["ok"] == 1


@pytest.mark.asyncio
async def test_estimate_aggregate_filter_injected_as_first_match():
    """非空 filter 必须作为首 $match 注入到 pipeline 头部."""
    captured: dict = {}

    async def fake_command(spec, **kw):
        captured["pipeline"] = spec["explain"]["pipeline"]
        return {"ok": 1, "stages": []}

    fake_client = MagicMock()
    fake_client.server_info = AsyncMock(return_value={"version": "6.0.0"})
    fake_db = MagicMock()
    fake_db.client = fake_client
    fake_db.command = AsyncMock(side_effect=fake_command)

    with patch(
        "app.engine.tools.cost_tools.get_mongo_db",
        new=AsyncMock(return_value=fake_db),
    ):
        from app.engine.tools.cost_tools import estimate_query_cost
        await estimate_query_cost(
            namespace_id=1, collection="c", filter={"k": "v"},
            database="d", sse_emit=AsyncMock(),
            pipeline_stages=[{"$group": {"_id": "$x"}}],
        )

    assert captured["pipeline"][0] == {"$match": {"k": "v"}}
    assert captured["pipeline"][1] == {"$group": {"_id": "$x"}}


@pytest.mark.asyncio
async def test_estimate_aggregate_empty_filter_no_match_injected():
    """空 filter 时不应注入 $match (避免 {} 全表 stage)."""
    captured: dict = {}

    async def fake_command(spec, **kw):
        captured["pipeline"] = spec["explain"]["pipeline"]
        return {"ok": 1, "stages": []}

    fake_client = MagicMock()
    fake_client.server_info = AsyncMock(return_value={"version": "6.0.0"})
    fake_db = MagicMock()
    fake_db.client = fake_client
    fake_db.command = AsyncMock(side_effect=fake_command)

    with patch(
        "app.engine.tools.cost_tools.get_mongo_db",
        new=AsyncMock(return_value=fake_db),
    ):
        from app.engine.tools.cost_tools import estimate_query_cost
        await estimate_query_cost(
            namespace_id=1, collection="c", filter={},
            database="d", sse_emit=AsyncMock(),
            pipeline_stages=[{"$count": "n"}],
        )

    assert captured["pipeline"] == [{"$count": "n"}]


def test_prune_explain_drops_metadata_and_truncates_long_arrays():
    """剪枝单元测试 — 元信息全删, 长数组截断带 _truncated 标记."""
    from app.engine.tools.cost_tools import (
        _EXPLAIN_MAX_ARRAY_ITEMS,
        _prune_explain,
    )

    long_list = list(range(_EXPLAIN_MAX_ARRAY_ITEMS + 5))
    pruned = _prune_explain({
        "$clusterTime": "x", "operationTime": "x", "serverInfo": "x",
        "rejectedPlans": [1, 2], "allPlansExecution": [1, 2],
        "command": "x",
        "stages": long_list,
        "keep_me": "v",
    })

    assert "$clusterTime" not in pruned
    assert "operationTime" not in pruned
    assert "serverInfo" not in pruned
    assert "rejectedPlans" not in pruned
    assert "allPlansExecution" not in pruned
    assert "command" not in pruned
    assert pruned["keep_me"] == "v"
    assert len(pruned["stages"]) == _EXPLAIN_MAX_ARRAY_ITEMS + 1
    assert pruned["stages"][-1] == {"_truncated": 5}


@pytest.mark.asyncio
async def test_empty_pipeline_stages_routes_to_find_path():
    """pipeline_stages=[] 与 None 等价, 走 find 路径返回结构化指标."""
    fake_explain = {
        "executionStats": {"totalDocsExamined": 7},
        "queryPlanner": {"winningPlan": {}},
    }
    fake_find = MagicMock()
    fake_find.explain = AsyncMock(return_value=fake_explain)
    fake_coll = MagicMock()
    fake_coll.find = MagicMock(return_value=fake_find)
    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_coll
    fake_db.command = AsyncMock(side_effect=AssertionError("空列表不应走 aggregate"))

    with patch(
        "app.engine.tools.cost_tools.get_mongo_db",
        new=AsyncMock(return_value=fake_db),
    ):
        from app.engine.tools.cost_tools import estimate_query_cost
        out = await estimate_query_cost(
            namespace_id=1, collection="c", filter={}, database="d",
            sse_emit=AsyncMock(),
            pipeline_stages=[],
        )

    assert out["estimated_docs"] == 7
    assert "explain_raw" not in out
    assert "mongo_version" not in out


@pytest.mark.asyncio
async def test_estimate_query_cost_with_empty_explain():
    """Empty explain (DocumentDB) → fallback count_documents."""
    fake_find = MagicMock()
    fake_find.explain = AsyncMock(return_value={})
    fake_coll = MagicMock()
    fake_coll.find = MagicMock(return_value=fake_find)
    fake_coll.count_documents = AsyncMock(return_value=42)
    fake_db = MagicMock()
    fake_db.__getitem__.return_value = fake_coll

    with patch(
        "app.engine.tools.cost_tools.get_mongo_db",
        new=AsyncMock(return_value=fake_db),
    ):
        from app.engine.tools.cost_tools import estimate_query_cost
        out = await estimate_query_cost(
            namespace_id=1, collection="c_product", database="testdb",
            filter={"x": 1}, sse_emit=AsyncMock(),
        )
    assert out["estimated_docs"] == 42
    assert out["hit_indexes"] == []
    assert out.get("warning") is None
