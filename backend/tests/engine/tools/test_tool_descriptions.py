"""lookup_knowledge + save_knowledge description 契约 (spec 2026-07-08 C6).

读写不对称: lookup 留 route_hint (改新 shape + references); save 删 route_hint 行.
"""
from app.engine.tools.registry import TOOL_SPECS

LOOKUP = next(t for t in TOOL_SPECS if t["name"] == "lookup_knowledge")["description"]
SAVE = next(t for t in TOOL_SPECS if t["name"] == "save_knowledge")["description"]


def test_lookup_route_hint_payload_new_shape():
    assert "navigation_note" in LOOKUP
    assert "question_pattern 见 content" not in LOOKUP


def test_lookup_references_described():
    assert "references" in LOOKUP
    assert "term" in LOOKUP and "target" in LOOKUP
    assert "database" in LOOKUP and "db_type" in LOOKUP
    assert "直接用" in LOOKUP  # 动作驱动


def test_lookup_no_leaked_gate_logic():
    assert "无 plan" not in LOOKUP
    assert "可能附" not in LOOKUP
    assert "再查一次" not in LOOKUP  # round 3: references 取代二次查 terminology
    assert "历史成功" not in LOOKUP  # round 3: 知识库三源 (手动/代码/agent), 不只 agent 历史
    assert "锚点" not in LOOKUP  # round 4: 不泄漏内部概念 (terminology anchors section), LLM 视角


def test_save_desc_no_route_hint():
    """C9 后 agent 不产 route_hint, save 描述不该再列它 (含 enum 与 payload 描述)."""
    assert "route_hint" not in SAVE


def test_save_desc_keeps_four_types():
    """save 描述保留可写四类."""
    for t in ("terminology", "instance_alias", "example", "rule"):
        assert t in SAVE


def test_save_desc_no_anchor():
    """round 5: save description 不泄漏'锚点'内部概念 (Anthropic 最佳实践)."""
    assert "锚点" not in SAVE
