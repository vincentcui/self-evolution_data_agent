"""5 路径 example KE 结构统一集成测试 — G1 验收.

验证 5 条写入路径产出的 example KE 结构一致:
1. code_extract  — _write_business_examples (extraction_writer.py)
2. agent_learn   — save_knowledge tool (knowledge_tools.py)
3. trace_refine  — refine endpoint → save_knowledge (agent_traces.py)
4. async_extract — _write_extract_results (query.py)
5. manual        — create_knowledge API → parse_payload (knowledge.py)

核心断言:
- content 是 NL 问题 (无 "查询模式:" 前缀)
- payload.question_pattern == content
- payload.final_query_plan 非空, steps[] 含 5 必要键
- parse_payload("example", payload) 通过 (extra=forbid schema 校验)
"""
import json
import uuid
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.git_repo import GitRepo
from app.models.knowledge_entry import KnowledgeEntry
from app.models.namespace import DataSource, Namespace
from app.schemas.knowledge_payload import parse_payload


# ════════════════════════════════════════════
#  Fixtures
# ════════════════════════════════════════════


@pytest_asyncio.fixture
async def seeded(db_session: AsyncSession) -> tuple[int, int]:
    """Namespace + DataSource + GitRepo seed, return (ns_id, repo_id)."""
    slug = f"t14_{uuid.uuid4().hex[:8]}"
    ns = Namespace(name=slug, slug=slug, description="5paths unified test")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)

    ds = DataSource(
        namespace_id=ns.id, db_type="mongodb", database="shop",
        host="localhost", port=27017, username="", password="",
    )
    db_session.add(ds)
    await db_session.commit()

    repo = GitRepo(namespace_id=ns.id, url="https://example.invalid/5p.git")
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return ns.id, repo.id


# ════════════════════════════════════════════
#  Helper: 统一结构断言
# ════════════════════════════════════════════

_STEP_REQUIRED_KEYS = {"db_type", "database", "collection", "operation", "query"}


def _assert_unified_example_structure(content: str, payload: dict, path_name: str):
    """G1: 验证 example KE 统一结构."""
    # content 非空且无旧前缀
    assert content, f"{path_name}: content 不应为空"
    assert not content.startswith("查询模式:"), f"{path_name}: content 不应有 '查询模式:' 前缀"

    # question_pattern == content
    assert payload["question_pattern"] == content, (
        f"{path_name}: payload.question_pattern 应等于 content"
    )

    # final_query_plan 非空, steps 结构完整
    plan = payload["final_query_plan"]
    assert plan, f"{path_name}: final_query_plan 不应为空"
    assert "steps" in plan and plan["steps"], f"{path_name}: final_query_plan.steps 不应为空"
    for i, step in enumerate(plan["steps"]):
        assert _STEP_REQUIRED_KEYS <= set(step.keys()), (
            f"{path_name}: step[{i}] 缺必要键, 有 {set(step.keys())}"
        )

    # parse_payload schema 校验通过 (extra=forbid)
    parse_payload("example", payload)  # 不抛即通过


# ════════════════════════════════════════════
#  Path 1: code_extract — _write_business_examples
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_path_code_extract(db_session: AsyncSession, seeded):
    """code_extract 路径: _write_business_examples 直调."""
    from app.knowledge.extraction_writer import _write_business_examples

    ns_id, repo_id = seeded
    ex = {
        "question": "查所有在售商品",
        "query": {"filter": {"active": True}},
        "operation": "filter",
        "tables": ["products"],
    }
    coll_to_db = {"products": ("mongodb", "shop")}
    n = await _write_business_examples(db_session, ns_id, repo_id, [ex], coll_to_db)
    assert n == 1

    rows = (await db_session.execute(
        select(KnowledgeEntry).where(
            KnowledgeEntry.namespace_id == ns_id,
            KnowledgeEntry.entry_type == "example",
            KnowledgeEntry.source == "code_extract",
        )
    )).scalars().all()
    assert len(rows) == 1

    ke = rows[0]
    payload = json.loads(ke.payload)
    _assert_unified_example_structure(ke.content, payload, "code_extract")


# ════════════════════════════════════════════
#  Path 2: agent_learn — save_knowledge tool
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_path_agent_learn(db_session: AsyncSession, seeded):
    """agent_learn 路径: save_knowledge tool 直调."""
    from app.engine.tools.knowledge_tools import save_knowledge

    ns_id, _repo_id = seeded
    ns = await db_session.get(Namespace, ns_id)
    content = "按状态分组统计订单数"
    payload = {
        "question_pattern": content,
        "collections": [{"database": "shop", "collection": "orders"}],
        "join_keys": [],
        "final_query_plan": {"steps": [{
            "db_type": "mongodb",
            "database": "shop",
            "collection": "orders",
            "operation": "aggregate",
            "query": {"pipeline": [{"$group": {"_id": "$status", "count": {"$sum": 1}}}]},
        }]},
        "result_summary": "按 status 字段分组并计数",
    }

    ret = await save_knowledge(
        db=db_session, namespace_id=ns_id, ns_slug=ns.slug,
        sse_emit=AsyncMock(),
        entry_type="example",
        content=content,
        payload=payload,
        evidence={"trace_ids": ["t_test"], "reasoning": "从本次查询提取"},
        tier="normal",
    )
    assert "entry_id" in ret, f"save_knowledge 应成功, got: {ret}"

    ke = await db_session.get(KnowledgeEntry, ret["entry_id"])
    assert ke is not None
    stored_payload = json.loads(ke.payload)
    _assert_unified_example_structure(ke.content, stored_payload, "agent_learn")


# ════════════════════════════════════════════
#  Path 3: trace_refine — 模拟 refine 端点产出结构
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_path_trace_refine(db_session: AsyncSession, seeded):
    """trace_refine 路径: refine 端点最终走 save_knowledge, 此处模拟其产出结构.

    agent_traces.py refine 端点:
    - LLM 产 question_pattern + result_summary
    - code 侧补 final_query_plan (normalize_query_plan) + collections (CollectionRef)
    - 最终走 save_knowledge(entry_type="example") 写入
    """
    from app.engine.tools.knowledge_tools import save_knowledge

    ns_id, _repo_id = seeded
    ns = await db_session.get(Namespace, ns_id)

    # 模拟 trace_refine 产出的 example payload (经 allowlist + code 补机械字段后)
    content = "查询最近7天每日新增用户数"
    payload = {
        "question_pattern": content,
        "result_summary": "按 created_at 日期分组统计用户数",
        "collections": [{"database": "shop", "collection": "users"}],
        "join_keys": [],
        "final_query_plan": {"steps": [{
            "db_type": "mongodb",
            "database": "shop",
            "collection": "users",
            "operation": "aggregate",
            "query": {"pipeline": [
                {"$match": {"created_at": {"$gte": "2026-07-15"}}},
                {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}}, "count": {"$sum": 1}}},
            ]},
        }]},
    }

    ret = await save_knowledge(
        db=db_session, namespace_id=ns_id, ns_slug=ns.slug,
        sse_emit=AsyncMock(),
        entry_type="example",
        content=content,
        payload=payload,
        evidence={"trace_ids": ["tr_001"], "reasoning": "trace refine 提炼"},
        tier="normal",
    )
    assert "entry_id" in ret, f"save_knowledge 应成功, got: {ret}"

    ke = await db_session.get(KnowledgeEntry, ret["entry_id"])
    assert ke is not None
    stored_payload = json.loads(ke.payload)
    _assert_unified_example_structure(ke.content, stored_payload, "trace_refine")


# ════════════════════════════════════════════
#  Path 4: async_extract — _write_extract_results
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_path_async_extract(db_session: AsyncSession, seeded):
    """async_extract 路径: _write_extract_results 直调."""
    from app.api.query import _write_extract_results

    ns_id, _repo_id = seeded
    question_pattern = "查询金额最大的前10笔订单"
    example_payload = {
        "question_pattern": question_pattern,
        "collections": [{"database": "shop", "collection": "orders"}],
        "join_keys": [],
        "final_query_plan": {"steps": [{
            "db_type": "mongodb",
            "database": "shop",
            "collection": "orders",
            "operation": "aggregate",
            "query": {"pipeline": [
                {"$sort": {"amount": -1}},
                {"$limit": 10},
            ]},
        }]},
        "result_summary": "按金额降序取前10",
    }

    await _write_extract_results(
        db=db_session, ns_id=ns_id,
        trace_id="trace_async_test",
        question_pattern=question_pattern,
        example=example_payload,
        evidence={"trace_ids": ["trace_async_test"]},
    )

    rows = (await db_session.execute(
        select(KnowledgeEntry).where(
            KnowledgeEntry.namespace_id == ns_id,
            KnowledgeEntry.entry_type == "example",
        )
    )).scalars().all()
    assert len(rows) == 1

    ke = rows[0]
    payload = json.loads(ke.payload)
    _assert_unified_example_structure(ke.content, payload, "async_extract")


# ════════════════════════════════════════════
#  Path 5: manual — create_knowledge API (parse_payload 校验)
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_path_manual(db_session: AsyncSession, seeded):
    """manual 路径: create_knowledge API 走 parse_payload 校验.

    此处直接构造 KE + parse_payload 校验, 验证 schema 接受手动路径的合法 payload.
    """
    ns_id, _repo_id = seeded
    content = "查每个用户的最近一笔订单"
    payload = {
        "question_pattern": content,
        "collections": [
            {"database": "shop", "collection": "users"},
            {"database": "shop", "collection": "orders"},
        ],
        "join_keys": [{"from": "orders.user_id", "to": "users.id"}],
        "final_query_plan": {"steps": [
            {
                "db_type": "mongodb",
                "database": "shop",
                "collection": "orders",
                "operation": "aggregate",
                "query": {"pipeline": [
                    {"$sort": {"created_at": -1}},
                    {"$group": {"_id": "$user_id", "last_order": {"$first": "$$ROOT"}}},
                ]},
            },
        ]},
        "result_summary": "按 user_id 分组取最新订单",
    }

    # parse_payload 校验通过 (extra=forbid)
    parsed = parse_payload("example", payload)
    assert parsed.question_pattern == content

    # 模拟 create_knowledge 写入
    validated_payload = parsed.model_dump(exclude_none=False)
    ke = KnowledgeEntry(
        namespace_id=ns_id,
        entry_type="example",
        content=content,
        tier="normal",
        status="proposed",
        source="manual",
        payload=json.dumps(validated_payload, ensure_ascii=False),
    )
    db_session.add(ke)
    await db_session.commit()
    await db_session.refresh(ke)

    stored_payload = json.loads(ke.payload)
    _assert_unified_example_structure(ke.content, stored_payload, "manual")


# ════════════════════════════════════════════
#  综合: 5 路径结构一致性
# ════════════════════════════════════════════


@pytest.mark.asyncio
async def test_5paths_all_pass_parse_payload(db_session: AsyncSession, seeded):
    """G1 综合: 5 路径 payload 全部通过 parse_payload("example", ...) 校验."""
    from app.knowledge.extraction_writer import _write_business_examples
    from app.engine.tools.knowledge_tools import save_knowledge
    from app.api.query import _write_extract_results

    ns_id, repo_id = seeded
    ns = await db_session.get(Namespace, ns_id)

    # 统一 question 和 step 模板
    questions = [
        "查所有在售商品",
        "按状态分组统计订单数",
        "查询最近7天每日新增用户数",
        "查询金额最大的前10笔订单",
        "查每个用户的最近一笔订单",
    ]

    def _make_payload(q: str, coll: str = "orders") -> dict:
        return {
            "question_pattern": q,
            "collections": [{"database": "shop", "collection": coll}],
            "join_keys": [],
            "final_query_plan": {"steps": [{
                "db_type": "mongodb",
                "database": "shop",
                "collection": coll,
                "operation": "filter",
                "query": {"filter": {"active": True}},
            }]},
            "result_summary": "",
        }

    # Path 1: code_extract
    ex = {"question": questions[0], "query": {"filter": {"active": True}},
          "operation": "filter", "tables": ["products"]}
    await _write_business_examples(
        db_session, ns_id, repo_id, [ex], {"products": ("mongodb", "shop")},
    )

    # Path 2: agent_learn
    await save_knowledge(
        db=db_session, namespace_id=ns_id, ns_slug=ns.slug,
        sse_emit=AsyncMock(), entry_type="example",
        content=questions[1], payload=_make_payload(questions[1]),
        evidence={}, tier="normal",
    )

    # Path 3: trace_refine (same as agent_learn — both go through save_knowledge)
    await save_knowledge(
        db=db_session, namespace_id=ns_id, ns_slug=ns.slug,
        sse_emit=AsyncMock(), entry_type="example",
        content=questions[2], payload=_make_payload(questions[2], "users"),
        evidence={}, tier="normal",
    )

    # Path 4: async_extract
    await _write_extract_results(
        db=db_session, ns_id=ns_id, trace_id="t14_async",
        question_pattern=questions[3],
        example=_make_payload(questions[3]),
        evidence={},
    )

    # Path 5: manual
    p5 = _make_payload(questions[4], "users")
    parsed = parse_payload("example", p5)
    ke5 = KnowledgeEntry(
        namespace_id=ns_id, entry_type="example", content=questions[4],
        tier="normal", status="proposed", source="manual",
        payload=json.dumps(parsed.model_dump(exclude_none=False), ensure_ascii=False),
    )
    db_session.add(ke5)
    await db_session.flush()

    # ── 验证: 所有 example KE 通过统一结构断言 ──
    all_kes = (await db_session.execute(
        select(KnowledgeEntry).where(
            KnowledgeEntry.namespace_id == ns_id,
            KnowledgeEntry.entry_type == "example",
        )
    )).scalars().all()
    assert len(all_kes) == 5, f"应有 5 条 example KE, 实际 {len(all_kes)}"

    for ke in all_kes:
        payload = json.loads(ke.payload)
        _assert_unified_example_structure(ke.content, payload, f"ke_{ke.id}")
