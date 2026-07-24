"""Task 5 契约测试 — trace refine 路径 example KE 含 question_pattern + final_query_plan.

锁定 agent_traces.py refine 产出的 example KE payload 形状:
  - question_pattern: 非空字符串 (LLM 产)
  - final_query_plan: {"steps": [...]} (normalize_query_plan 代码补)
  - collections: list[CollectionRef] (code 无条件覆写)
  - join_keys: list[dict] (从 plan 抽)

防 T7 收紧 schema 后回归破坏.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models import AgentTrace
from app.models.knowledge_entry import KnowledgeEntry


# ── 含 execute_query 的 trace_json, 让 normalize_query_plan 能产出 final_query_plan ──
TRACE_JSON_WITH_EXECUTE_QUERY = json.dumps({
    "tool_trace": [
        {"name": "lookup_knowledge", "input": {"query": "活跃用户"}},
        {
            "name": "execute_query",
            "input": {
                "target": "orders",
                "database": "shop_db",
                "db_type": "mongodb",
                "query": {"pipeline": [{"$match": {"status": "active"}}]},
            },
        },
    ]
})


@pytest.mark.asyncio
async def test_refine_example_ke_has_question_pattern_and_final_query_plan(
    db, admin_client,
):
    """trace refine 产的 example KE: content=NL, payload 含 question_pattern + final_query_plan."""
    from app.knowledge.trace_refiner import ProposedKE

    # 种一条 completed trace, 含 execute_query tool_trace
    tr = AgentTrace(
        trace_id="refine-example-contract-1",
        namespace_id=None,
        user_query="本月活跃用户订单",
        status="completed",
        trace_json=TRACE_JSON_WITH_EXECUTE_QUERY,
    )
    db.add(tr)
    await db.commit()

    # mock refine_traces 返回 1 条 example 提案 (模拟 LLM 产出)
    fake_results = [ProposedKE(
        entry_type="example",
        content="查询本月活跃用户的订单",
        payload={
            "question_pattern": "查询本月活跃用户的订单",
            "result_summary": "按 user_id 过滤本月订单",
        },
        evidence={"trace_ids": ["refine-example-contract-1"], "reasoning": "trace 内..."},
        source_trace_id="refine-example-contract-1",
    )]

    with patch("app.knowledge.trace_refiner.refine_traces", return_value=fake_results):
        resp = await admin_client.post(
            "/api/agent-traces/refine",
            json={"trace_ids": ["refine-example-contract-1"]},
        )

    assert resp.status_code == 200
    out = resp.json()
    assert out["proposed_count"] == 1
    assert len(out["proposed_ke_ids"]) == 1

    # 查产出的 KnowledgeEntry
    ke_id = out["proposed_ke_ids"][0]
    ke = (await db.execute(
        select(KnowledgeEntry).where(KnowledgeEntry.id == ke_id)
    )).scalar_one()

    assert ke.entry_type == "example"
    assert ke.content  # content 非空 (NL)

    # ── 核心契约断言 ──
    payload = json.loads(ke.payload)
    assert "question_pattern" in payload and payload["question_pattern"], (
        f"payload 缺 question_pattern: {payload.keys()}"
    )
    assert "final_query_plan" in payload and payload["final_query_plan"], (
        f"payload 缺 final_query_plan: {payload.keys()}"
    )
    assert "steps" in payload["final_query_plan"], (
        f"final_query_plan 缺 steps: {payload['final_query_plan']}"
    )
    assert len(payload["final_query_plan"]["steps"]) > 0, "final_query_plan.steps 不应为空"

    # collections 应为 list[CollectionRef] 形态
    assert "collections" in payload
    assert isinstance(payload["collections"], list)
    if payload["collections"]:
        ref = payload["collections"][0]
        assert "database" in ref and "collection" in ref, (
            f"collections 应为 CollectionRef 形态: {ref}"
        )


@pytest.mark.asyncio
async def test_refine_example_ke_final_query_plan_step_shape(db, admin_client):
    """final_query_plan.steps[0] 含 db_type/database/collection/operation/query."""
    from app.knowledge.trace_refiner import ProposedKE

    tr = AgentTrace(
        trace_id="refine-example-contract-2",
        namespace_id=None,
        user_query="统计各状态订单数",
        status="completed",
        trace_json=TRACE_JSON_WITH_EXECUTE_QUERY,
    )
    db.add(tr)
    await db.commit()

    fake_results = [ProposedKE(
        entry_type="example",
        content="统计各状态订单数",
        payload={"question_pattern": "统计各状态订单数"},
        evidence={"trace_ids": ["refine-example-contract-2"]},
        source_trace_id="refine-example-contract-2",
    )]

    with patch("app.knowledge.trace_refiner.refine_traces", return_value=fake_results):
        resp = await admin_client.post(
            "/api/agent-traces/refine",
            json={"trace_ids": ["refine-example-contract-2"]},
        )

    assert resp.status_code == 200
    ke_id = resp.json()["proposed_ke_ids"][0]
    ke = (await db.execute(
        select(KnowledgeEntry).where(KnowledgeEntry.id == ke_id)
    )).scalar_one()

    payload = json.loads(ke.payload)
    step = payload["final_query_plan"]["steps"][0]
    # step 应含标准字段
    assert "db_type" in step
    assert "database" in step
    assert "collection" in step
    assert "operation" in step
    assert "query" in step
    assert step["db_type"] == "mongodb"
    assert step["database"] == "shop_db"
    assert step["collection"] == "orders"
