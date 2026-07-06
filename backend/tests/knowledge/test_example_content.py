"""Phase 2 P2.T13: NL paraphrases 索引升级 — 单元测试"""
import pytest
from pydantic import ValidationError

from app.knowledge.knowledge_content import build_example_content
from app.schemas.knowledge_payload import ExamplePayload, RulePayload


# ════════════════════════════════════════════
#  build_example_content
# ════════════════════════════════════════════


def test_build_example_content_with_paraphrases():
    payload = {
        "question": "昨天订单数",
        "nl_paraphrases": ["昨天有多少订单", "yesterday order count"],
    }
    result = build_example_content(payload)
    assert result == "昨天订单数\n昨天有多少订单\nyesterday order count"


def test_build_example_content_without_paraphrases():
    """向后兼容: 无 nl_paraphrases 时只返回 question"""
    payload = {
        "question": "昨天订单数",
        "target_collection": "c_product",
        "query_json": {},
    }
    result = build_example_content(payload)
    assert result == "昨天订单数"


def test_build_example_content_empty_paraphrases():
    """nl_paraphrases 为空列表时等同于无 paraphrases"""
    payload = {"question": "查询用户数", "nl_paraphrases": []}
    result = build_example_content(payload)
    assert result == "查询用户数"


def test_build_example_content_filters_empty_strings():
    """空字符串 paraphrase 被过滤"""
    payload = {"question": "查询", "nl_paraphrases": ["", "等价问法", ""]}
    result = build_example_content(payload)
    assert result == "查询\n等价问法"


def test_build_example_content_no_question():
    """question 为空时只拼 paraphrases"""
    payload = {"question": "", "nl_paraphrases": ["问法A", "问法B"]}
    result = build_example_content(payload)
    assert result == "问法A\n问法B"


# ════════════════════════════════════════════
#  ExamplePayload 新字段默认值
# ════════════════════════════════════════════


# ════════════════════════════════════════════
#  ExamplePayload 五字段 schema (Phase 1)
# ════════════════════════════════════════════


def test_example_payload_five_field_defaults():
    """五字段 schema: question_pattern 必填，其余有默认值."""
    p = ExamplePayload(question_pattern="按状态统计订单数")
    assert p.question_pattern == "按状态统计订单数"
    assert p.collections == []
    assert p.join_keys == []
    assert p.final_query_plan is None
    assert p.result_summary == ""


def test_example_payload_five_field_full():
    """新字段可正常赋值."""
    p = ExamplePayload(
        question_pattern="订单关联用户",
        collections=["orders", "users"],
        join_keys=[{"from": "orders.user_id", "to": "users.id"}],
        final_query_plan={
            "steps": [{"db_type": "mysql", "database": "shop", "collection": "orders",
                       "operation": "sql", "query": {"sql": "SELECT ..."}}],
        },
        result_summary="在orders上JOIN users关联",
    )
    assert p.collections == ["orders", "users"]
    assert len(p.join_keys) == 1
    assert p.join_keys[0]["from"] == "orders.user_id"
    assert p.final_query_plan["steps"][0]["db_type"] == "mysql"
    assert p.result_summary == "在orders上JOIN users关联"


def test_example_payload_extra_allow_accepts_unknown():
    """extra='allow' — 未知字段被接受，存入 model_extra."""
    p = ExamplePayload(
        question_pattern="查询用户",
        unknown_field="should_be_accepted_now",
        legacy_col="c_product",
    )
    assert p.question_pattern == "查询用户"
    assert p.model_extra is not None
    assert p.model_extra["unknown_field"] == "should_be_accepted_now"
    assert p.model_extra["legacy_col"] == "c_product"


# ════════════════════════════════════════════
#  RulePayload 新字段默认值
# ════════════════════════════════════════════


def test_rule_payload_new_fields_have_defaults():
    """RulePayload 新增字段有默认值"""
    p = RulePayload(rule_text="status=4 排除")
    assert p.rule_kind == "business_constraint"
    assert p.evidence is None


def test_rule_payload_with_new_fields():
    p = RulePayload(
        rule_text="is_deleted=0 必加",
        rule_kind="filter_default",
        evidence={"source": "mybatis", "repo_id": 1, "frequency": 95},
    )
    assert p.rule_kind == "filter_default"
    assert p.evidence == {"source": "mybatis", "repo_id": 1, "frequency": 95}


def test_rule_payload_invalid_rule_kind():
    with pytest.raises(ValidationError):
        RulePayload(rule_text="x", rule_kind="invalid_kind")  # type: ignore[arg-type]
