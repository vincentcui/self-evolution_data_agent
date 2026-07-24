"""Phase 4 Task 4.5: query.py 接通 load_all_knowledge → system_prompt anchors 注入.

集成验证: bundle 两段 (critical / terminology) 经 build_system_prompt
按 KnowledgeBundle.to_prompt_sections() 渲染规则正确进入 prompt, 等价 query.py
两个调用方 (_execute_via_agent_loop / query_stream) 的真实链路.
route_hint 已随 C5/C1 从 system prompt 摘除, 改为纯人工录入 + agent 主动 lookup_knowledge 召回.

不直接调 FastAPI 端点 — 端点路径覆盖在 test_query_api_agent.py;
本文件锁住"bundle → prompt 字符串"这一关键一跳, 防止未来任何调用方
忘传 anchors / critical 时静默丢失上下文.
"""
from __future__ import annotations

import json
import os

import pytest
import pytest_asyncio

from app.config import settings
from app.engine.tools.registry import build_system_prompt
from app.knowledge.knowledge_loader import load_all_knowledge
from app.knowledge.knowledge_retriever import upsert_knowledge_entry
from app.models.knowledge_entry import KnowledgeEntry
from app.models.namespace import Namespace

# ════════════════════════════════════════════
#  fixtures — 复用 agent_loop conftest 的 db_session + chroma_isolated
# ════════════════════════════════════════════

@pytest_asyncio.fixture
async def seeded_ns_full(db_session, chroma_isolated) -> tuple[Namespace, int]:
    """ns + 1 critical rule (SQL) + 1 canonical terminology (向量化) — 模拟真实 query 入口前置状态."""
    ns = Namespace(name="anchor_ns", slug="anchor_ns", description="phase4-task4.5")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)

    # critical rule (走 SQL 直加载, 不入 ChromaDB)
    db_session.add(KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="rule",
        status="canonical",
        tier="critical",
        content="默认 latestVersion=true",
        source="manual",
    ))

    # terminology (向量化入库)
    term_payload = {
        "term": "商品",
        "primary_collection": "c_category",
        "primary_database": "db_q",
        "db_type": "mongodb",
        "synonyms": ["货品"],
        "source_collections": ["c_category"],
    }
    term_ke = KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="terminology",
        status="canonical",
        tier="normal",
        content="商品 对应 c_category 集合",
        source="manual",
        payload=json.dumps(term_payload),
    )
    db_session.add(term_ke)
    await db_session.commit()
    await db_session.refresh(term_ke)

    upsert_knowledge_entry(
        slug=ns.slug,
        entry_id=term_ke.id,
        content=term_ke.content,
        tier="normal",
        namespace_id=ns.id,
        entry_type="terminology",
        status="canonical",
        payload=term_payload,
    )
    return ns, term_ke.id


# ════════════════════════════════════════════
#  Test 1 — bundle 落 prompt: terminology anchor
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_anchors_render_in_system_prompt_after_load(seeded_ns_full, db_session):
    """load_all_knowledge + AC 自动机 → build_system_prompt 输出含 '商品' / 'c_category'."""
    ns, _ = seeded_ns_full
    bundle = await load_all_knowledge(db_session, ns.id, ns.slug, "商品有几本？")

    # AC 自动机匹配 (需要先初始化)
    from app.knowledge.knowledge_loader import batch_load_terminology
    from app.knowledge.terminology_automaton import match_terminology, rebuild
    await rebuild(db_session, ns.id, ns.slug)
    term_ids = match_terminology(ns.slug, "商品有几本？")
    anchors = await batch_load_terminology(db_session, term_ids)

    prompt = build_system_prompt(
        settings=settings, namespace=ns,
        anchors=anchors,
        critical=bundle.critical,
    )

    # critical 段 (SQL 直加载始终命中, 不依赖向量召回)
    assert "默认 latestVersion=true" in prompt
    assert "## 关键规则 (critical)" in prompt

    # terminology 段 — 至少含 term / primary_collection 标识
    assert "## 业务术语锚点 (terminology)" in prompt
    assert "商品" in prompt
    assert "c_category" in prompt
    assert "db_q" in prompt
    # synonyms 渲染
    assert "(同义: 货品)" in prompt


# ════════════════════════════════════════════
#  Test 2 — critical 段独立验证: 即使没向量召回, critical 仍可达
# ════════════════════════════════════════════

@pytest.mark.asyncio
async def test_critical_renders_in_system_prompt(db_session, chroma_isolated):
    """只有 critical KE 时 → critical_section 渲染, anchors 不出现."""
    ns = Namespace(name="crit_only_ns", slug="crit_only_ns", description="phase4-crit-only")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)
    db_session.add(KnowledgeEntry(
        namespace_id=ns.id,
        entry_type="rule",
        status="canonical",
        tier="critical",
        content="禁止全表扫描",
        source="manual",
    ))
    await db_session.commit()

    bundle = await load_all_knowledge(db_session, ns.id, ns.slug, "随便问个问题")

    prompt = build_system_prompt(
        settings=settings, namespace=ns,
        anchors=[],
        critical=bundle.critical,
    )

    assert "## 关键规则 (critical)" in prompt
    assert "禁止全表扫描" in prompt
    # 无 terminology 时, 对应 section 标题不应出现; route_hint 已摘除, 恒不出现
    assert "## 业务术语锚点 (terminology)" not in prompt
    assert "## 路由提示 (route_hint)" not in prompt


# ════════════════════════════════════════════
#  Test 3 — 空 bundle: 两段标题全不出现 (兼容老调用方)
# ════════════════════════════════════════════

def test_empty_bundle_no_section_markers():
    """空 bundle (无 critical / 无召回) → prompt 不带任何 section 标题, 仅模板骨架."""
    fake_ns = Namespace(name="dummy", slug="dummy", description="")
    prompt = build_system_prompt(
        settings=settings, namespace=fake_ns,
        anchors=[],
        critical=[],
    )
    # section 标题在空时不出现
    assert "## 关键规则 (critical)" not in prompt
    assert "## 业务术语锚点 (terminology)" not in prompt
    assert "## 路由提示 (route_hint)" not in prompt
    # 但模板骨架仍在 (max_iter / 错误消费与循环规避字样可校验存活)
    assert "错误消费与循环规避" in prompt


# ════════════════════════════════════════════
#  Test 4 — route_hint 已从 system prompt 摘除 (spec 2026-07-07/07-08)
#  route_hint entry 仍在向量集合可被 lookup_knowledge 召回, 不再存在 route_hints 参数
# ════════════════════════════════════════════

def test_route_hint_not_injected_even_when_present():
    """route_hint 从 system prompt 摘除 — build_system_prompt 无 route_hints 参数, 恒不渲染."""
    fake_ns = Namespace(name="dummy2", slug="dummy2", description="")
    prompt = build_system_prompt(
        settings=settings, namespace=fake_ns,
        anchors=[], critical=[],
    )
    assert "## 路由提示 (route_hint)" not in prompt
    assert "orders → users" not in prompt


# ════════════════════════════════════════════
#  Test 5 — 真 LLM 端到端 (gated, 默认 skip — 真 LLM 验证归 Phase 5 acceptance)
# ════════════════════════════════════════════

@pytest.mark.skipif(
    not (os.getenv("IS_CLAUDE_API_KEY") or os.getenv("IS_LLM_API_KEY")),
    reason="real LLM API key not configured (deferred to Phase 5 acceptance)",
)
@pytest.mark.real_llm
@pytest.mark.asyncio
async def test_anchored_query_first_tool_targets_correct_collection():
    """Real LLM 验证: anchors 注入后, 首个 fetch_schema 应针对 c_category.

    Phase 4 Task 4.5 不门控真 LLM — 真实验证留 Phase 5 acceptance gate.
    """
    pytest.skip("real LLM e2e gated to Phase 5 acceptance phase")
