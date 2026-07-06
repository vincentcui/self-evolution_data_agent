# ============================================================================
# Phase 7 — _async_extract_after_end_turn 触发条件满足时的 happy path
# ----------------------------------------------------------------------------
# Stage extractor-protocol Task 2 升级: LLM 仅返 question_pattern + route_hint_reason.
# 机械字段 (final_pipeline / collections / field_mappings / chart_type / tool_count /
# join_fields / cost_strategy) 由代码侧抽取保真.
# ============================================================================
"""Phase 7: end_turn + tool_count >= min + 有 rows → 触发 LLM 抽取."""
import json
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.models.knowledge_entry import KnowledgeEntry


def _make_fake_result(tool_calls: list[dict], stop_reason: str = "end_turn"):
    from app.engine.agent_loop import AgentResult
    return AgentResult(
        final_answer="ok",
        iterations=len(tool_calls),
        stop_reason=stop_reason,
        tool_trace=tool_calls,
        usage_total={},
    )


@pytest.mark.asyncio
async def test_multi_collection_success_creates_example_and_route_hint(
    async_session, real_chromadb, seeded_ns_with_mongo_ds,
):
    from app.api import query as query_module

    ns_id, _repo_id = seeded_ns_with_mongo_ds
    fake_trace = [
        {"name": "fetch_schema", "input": {"target": "c_product", "db_type": "mongodb"},
         "output": {}, "status": "ok"},
        {"name": "fetch_schema", "input": {"target": "c_category_group", "db_type": "mongodb"},
         "output": {}, "status": "ok"},
        {"name": "inspect_values",
         "input": {"target": "c_product", "field": "categoryId"},
         "output": {}, "status": "ok"},
        {"name": "execute_plan",
         "input": {"plan": {"steps": [{
             "collection": "c_product",
             "pipeline": [{"$lookup": {
                 "from": "c_category_group", "localField": "categoryId",
                 "foreignField": "_id", "as": "category",
             }}],
         }]}},
         "output": {"rows": [{"x": 1}, {"x": 2}]}, "status": "ok"},
        {"name": "present_result", "input": {},
         "output": {"status": "ok", "ref": "call_plan",
                    "chart_spec": {"chart_type": "bar", "x": "x"}}, "status": "ok"},
    ]
    fake_result = _make_fake_result(fake_trace, "end_turn")

    # 新协议: LLM 只返两字段
    fake_llm_output = json.dumps({
        "question_pattern": "某商品的订单数量",
        "route_hint_reason": "商品→订单两层关联",
    }, ensure_ascii=False)

    def _fake_chat_completion(messages):
        del messages
        return fake_llm_output

    with patch("app.engine.llm.chat_completion", side_effect=_fake_chat_completion):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _test_session():
            async with async_session() as s:
                yield s

        with patch("app.api.query._new_db_session", _test_session):
            await query_module._async_extract_after_end_turn(
                ns_id=ns_id, ns_slug="test_ns", question="商品→订单",
                result=fake_result, trace_id="t-123",
            )

    async with async_session() as db:
        entries = (await db.execute(
            select(KnowledgeEntry).where(
                KnowledgeEntry.namespace_id == ns_id,
                KnowledgeEntry.source == "agent_learn",
            )
        )).scalars().all()

    types = {e.entry_type for e in entries}
    assert "example" in types
    assert "route_hint" in types
    example_ke = next(e for e in entries if e.entry_type == "example")
    assert example_ke.status == "proposed"
    assert example_ke.content == "某商品的订单数量"
    payload = json.loads(example_ke.payload)
    assert payload["question_pattern"] == "某商品的订单数量"
    # 新 5 字段 schema: question_pattern / collections / join_keys / final_query_plan / result_summary
    assert payload["collections"] == ["c_product", "c_category_group"]
    assert payload.get("join_keys") is not None
    # final_query_plan 含真实 $lookup, 不再是空壳
    lookup_from = payload["final_query_plan"]["steps"][0]["pipeline"][0]["$lookup"]["from"]
    assert lookup_from == "c_category_group"
    # 旧字段 chart_type / tool_count / field_mappings 已移除 (移至 evidence)

    rh_ke = next(e for e in entries if e.entry_type == "route_hint")
    rh_payload = json.loads(rh_ke.payload)
    assert rh_payload["reason"] == "商品→订单两层关联"
    assert rh_payload["join_fields"] == [{"a": "c_product.categoryId", "b": "c_category_group._id"}]
    assert rh_payload["cost_strategy"] == "default"
    assert rh_payload["collection_path"] == ["c_product", "c_category_group"]
