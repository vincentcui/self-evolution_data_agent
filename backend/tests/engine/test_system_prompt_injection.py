"""Phase 4 Task 4.4: system_prompt 注入 critical / anchors / route_hints 3 段."""
from __future__ import annotations

from dataclasses import dataclass

from app.engine.tools.registry import build_system_prompt
from app.knowledge.knowledge_loader import RouteHintCandidate, TerminologyAnchor


@dataclass
class _MockSettings:
    query_cost_single_layer_limit: int = 50_000
    query_cost_total_limit: int = 5_000_000
    agent_reflection_enabled: bool = False


@dataclass
class _MockNs:
    id: int = 1
    slug: str = "test"


def test_renders_3_sections_when_full():
    anchors = [TerminologyAnchor(
        term="商品", target="c_category",
        database="db_q", db_type="mongodb",
        synonyms=["货品"], source_collections=["c_category"],
    )]
    rh = [RouteHintCandidate(
        question_pattern="商品→订单",
        collection_path=["c_category", "c_product"],
        cost_strategy="batched_count_only",
        reason="多层连乘",
    )]
    prompt = build_system_prompt(
        settings=_MockSettings(), namespace=_MockNs(),
        anchors=anchors, critical=["默认 latestVersion=true"], route_hints=rh,
    )
    # KnowledgeBundle 渲染的 section header — base prompt 不含
    assert "## 关键规则 (critical)" in prompt
    assert "## 业务术语锚点 (terminology)" in prompt
    assert "## 路由提示 (route_hint)" in prompt
    assert "商品" in prompt
    assert "c_category" in prompt
    assert "默认 latestVersion=true" in prompt


def test_omits_empty_sections():
    prompt = build_system_prompt(
        settings=_MockSettings(), namespace=_MockNs(),
        anchors=[], critical=[], route_hints=[],
    )
    # 三个 section header 在空 bundle 时一律不渲染
    assert "## 关键规则 (critical)" not in prompt
    assert "## 业务术语锚点 (terminology)" not in prompt
    assert "## 路由提示 (route_hint)" not in prompt


def test_cost_limits_filled():
    settings = _MockSettings(
        query_cost_single_layer_limit=500_000,
        query_cost_total_limit=10_000_000,
    )
    prompt = build_system_prompt(
        settings=settings, namespace=_MockNs(),
        anchors=[], critical=[], route_hints=[],
    )
    assert "500,000" in prompt
    assert "10,000,000" in prompt


def test_backward_compat_no_kwargs():
    """旧调用方仅传 settings/namespace, 不传 anchors/critical/route_hints, 应等价于全空."""
    prompt = build_system_prompt(settings=_MockSettings(), namespace=_MockNs())
    assert "## 关键规则 (critical)" not in prompt
    assert "## 业务术语锚点 (terminology)" not in prompt
    assert "## 路由提示 (route_hint)" not in prompt
    # 仍然渲染基础 prompt 内容
    assert "lookup_knowledge" in prompt
    # plan03 Task5: 步骤8 提 db_profile 能力限制 (非 server_capabilities)
    assert "db_profile" in prompt
    assert "server_capabilities" not in prompt
    # 2026-05-12 reform: 不再向 LLM 暴露迭代配额
    assert "迭代上限" not in prompt
