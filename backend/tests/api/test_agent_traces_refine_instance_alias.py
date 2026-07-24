"""Phase 7 Task 17b 契约测试 — trace-refine 路径 instance_alias db_type 传播.

锁定 agent_traces.py refine 产出的 instance_alias KE:
  - 正向: trace 含 db_type (extract_db_context) → payload 含 db_type → validate 通过入库
  - 负向: trace 无 db_type 且 LLM 未产 → validate 拒 → 不入库
  - allowlist: LLM 产的 db_type / target_database / target_collection 不被剥
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models import AgentTrace
from app.models.knowledge_entry import KnowledgeEntry


# ── 含 db_type 上下文的 trace (extract_db_context 可抽) ──
TRACE_JSON_WITH_DB_CONTEXT = json.dumps({
    "tool_trace": [
        {
            "name": "execute_query",
            "input": {
                "target": "user_levels",
                "database": "shop",
                "db_type": "mongodb",
                "query": {"filter": {"_id": "5f8a1b2c3d4e5f6a7b8c9d0e"}},
            },
        },
    ]
})

# ── 无 db_type 上下文的 trace ──
TRACE_JSON_NO_DB_CONTEXT = json.dumps({
    "tool_trace": [
        {"name": "lookup_knowledge", "input": {"query": "金牌会员"}},
    ]
})


@pytest.mark.asyncio
async def test_refine_instance_alias_db_type_from_trace(db, admin_client):
    """正向: trace 有 db_type → payload 含 db_type + validate 通过 → 入库."""
    from app.knowledge.trace_refiner import ProposedKE

    tr = AgentTrace(
        trace_id="refine-ia-dbtype-1",
        namespace_id=None,
        user_query="金牌会员的等级信息",
        status="completed",
        trace_json=TRACE_JSON_WITH_DB_CONTEXT,
    )
    db.add(tr)
    await db.commit()

    fake_results = [ProposedKE(
        entry_type="instance_alias",
        content="金牌会员",
        payload={
            "alias": "金牌会员",
            "canonical_name": "金牌会员等级",
            "target_id": "5f8a1b2c3d4e5f6a7b8c9d0e",
            "id_field": "_id",
        },
        evidence={"trace_ids": ["refine-ia-dbtype-1"], "reasoning": "trace 内..."},
        source_trace_id="refine-ia-dbtype-1",
    )]

    with patch("app.knowledge.trace_refiner.refine_traces", return_value=fake_results):
        resp = await admin_client.post(
            "/api/agent-traces/refine",
            json={"trace_ids": ["refine-ia-dbtype-1"]},
        )

    assert resp.status_code == 200
    out = resp.json()
    assert out["proposed_count"] == 1
    assert len(out["proposed_ke_ids"]) == 1, "db_type 回填后 validate 应通过入库"

    ke_id = out["proposed_ke_ids"][0]
    ke = (await db.execute(
        select(KnowledgeEntry).where(KnowledgeEntry.id == ke_id)
    )).scalar_one()

    payload = json.loads(ke.payload)
    assert payload["db_type"] == "mongodb"
    assert payload["target_database"] == "shop"
    assert payload["target_collection"] == "user_levels"
    assert payload["target_id"] == "5f8a1b2c3d4e5f6a7b8c9d0e"


@pytest.mark.asyncio
async def test_refine_instance_alias_llm_db_type_not_stripped(db, admin_client):
    """allowlist: LLM 产的 db_type/target_database/target_collection 不被剥."""
    from app.knowledge.trace_refiner import ProposedKE

    tr = AgentTrace(
        trace_id="refine-ia-dbtype-2",
        namespace_id=None,
        user_query="旗舰商品是哪个",
        status="completed",
        trace_json=TRACE_JSON_NO_DB_CONTEXT,
    )
    db.add(tr)
    await db.commit()

    # LLM 自行产出 db_type + target_database + target_collection
    fake_results = [ProposedKE(
        entry_type="instance_alias",
        content="旗舰商品",
        payload={
            "alias": "旗舰商品",
            "canonical_name": "旗舰商品 p_007",
            "target_id": "p_007",
            "id_field": "product_id",
            "target_database": "shop",
            "target_collection": "products",
            "db_type": "mongodb",
        },
        evidence={"trace_ids": ["refine-ia-dbtype-2"], "reasoning": "LLM 语义判断"},
        source_trace_id="refine-ia-dbtype-2",
    )]

    with patch("app.knowledge.trace_refiner.refine_traces", return_value=fake_results):
        resp = await admin_client.post(
            "/api/agent-traces/refine",
            json={"trace_ids": ["refine-ia-dbtype-2"]},
        )

    assert resp.status_code == 200
    out = resp.json()
    assert len(out["proposed_ke_ids"]) == 1, "LLM 产的 db_type 不应被 allowlist 剥"

    ke_id = out["proposed_ke_ids"][0]
    ke = (await db.execute(
        select(KnowledgeEntry).where(KnowledgeEntry.id == ke_id)
    )).scalar_one()

    payload = json.loads(ke.payload)
    assert payload["db_type"] == "mongodb"
    assert payload["target_database"] == "shop"
    assert payload["target_collection"] == "products"


@pytest.mark.asyncio
async def test_refine_instance_alias_no_db_type_rejected(db, admin_client):
    """负向: trace 无 db_type 且 LLM 未产 → validate 拒 → 不入库."""
    from app.knowledge.trace_refiner import ProposedKE

    tr = AgentTrace(
        trace_id="refine-ia-dbtype-3",
        namespace_id=None,
        user_query="那个大客户的联系方式",
        status="completed",
        trace_json=TRACE_JSON_NO_DB_CONTEXT,
    )
    db.add(tr)
    await db.commit()

    fake_results = [ProposedKE(
        entry_type="instance_alias",
        content="大客户",
        payload={
            "alias": "大客户",
            "canonical_name": "大客户 A",
            "target_id": "c_999",
            "id_field": "_id",
            # 无 db_type, trace 也无 → validate 必拒
        },
        evidence={"trace_ids": ["refine-ia-dbtype-3"], "reasoning": "..."},
        source_trace_id="refine-ia-dbtype-3",
    )]

    with patch("app.knowledge.trace_refiner.refine_traces", return_value=fake_results):
        resp = await admin_client.post(
            "/api/agent-traces/refine",
            json={"trace_ids": ["refine-ia-dbtype-3"]},
        )

    assert resp.status_code == 200
    out = resp.json()
    assert out["proposed_count"] == 1
    assert len(out["proposed_ke_ids"]) == 0, "缺 db_type 应被 validate 拒绝, 不入库"
