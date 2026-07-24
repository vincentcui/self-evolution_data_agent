"""ExamplePayload schema 收紧测试 — extra=forbid + final_query_plan 必填 + 死字段拒绝."""
import pytest

from app.schemas.knowledge_payload import ExamplePayload


def test_example_payload_requires_question_pattern_and_final_query_plan():
    """缺 final_query_plan 或 question_pattern 应报错."""
    with pytest.raises(Exception):
        ExamplePayload(question_pattern="q")  # 缺 final_query_plan
    with pytest.raises(Exception):
        ExamplePayload(final_query_plan={"steps": []})  # 缺 question_pattern


def test_example_payload_extra_forbid_rejects_dead_fields():
    """extra='forbid' 拒绝死字段 (nl_paraphrases 等)."""
    with pytest.raises(Exception):
        ExamplePayload(
            question_pattern="q",
            final_query_plan={"steps": []},
            nl_paraphrases=["x"],  # 死字段, forbid 拒绝
        )


def test_example_payload_accepts_minimal_valid():
    """最小合法 payload 通过校验."""
    p = ExamplePayload(
        question_pattern="查在售商品",
        final_query_plan={"steps": [{
            "db_type": "mongodb", "database": "shop",
            "collection": "products", "operation": "filter",
            "query": {"filter": {"active": True}},
        }]},
    )
    assert p.question_pattern == "查在售商品"
    assert p.final_query_plan["steps"][0]["collection"] == "products"
