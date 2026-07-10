"""Stage extractor-protocol Task 2 — 机械字段必须由代码抽取, 不经 LLM.

哲学: 机械字段不变性收敛在代码侧, LLM 只负责语义改写 (question_pattern + result_summary).
route_hint 专属机械字段 (join_fields / cost_strategy) 已随 C5 死代码清理删除
(route_hint 收敛为纯人工录入, 不再有代码侧抽取).
"""
import pytest

from app.api.query import (
    _validate_llm_output_minimal,
)


def test_validate_llm_output_minimal_accepts_two_fields():
    _validate_llm_output_minimal(
        {"question_pattern": "某商品的订单数量", "result_summary": "两层关联"}
    )
    _validate_llm_output_minimal({"question_pattern": "某x", "result_summary": None})


def test_validate_llm_output_minimal_rejects_missing_pattern():
    with pytest.raises(ValueError, match="question_pattern"):
        _validate_llm_output_minimal({"result_summary": "x"})
