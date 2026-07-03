import pytest
from pydantic import ValidationError

from app.schemas.knowledge_payload import TerminologyPayload


VALID = {
    "term": "商品", "primary_collection": "c_category",
    "primary_database": "db_q", "db_type": "mongodb",
    "synonyms": ["货品", "存货"], "source_collections": ["c_category"],
}


def test_valid_payload():
    p = TerminologyPayload(**VALID)
    assert p.term == "商品" and p.db_type == "mongodb"


def test_term_empty_rejected():
    with pytest.raises(ValidationError):
        TerminologyPayload(**{**VALID, "term": "  "})


def test_term_too_long_rejected():
    with pytest.raises(ValidationError, match="单一业务名词"):
        TerminologyPayload(**{**VALID, "term": "货" * 21})


def test_term_with_newline_rejected():
    with pytest.raises(ValidationError, match="换行"):
        TerminologyPayload(**{**VALID, "term": "商\n品"})


def test_term_with_period_rejected():
    with pytest.raises(ValidationError, match="句号"):
        TerminologyPayload(**{**VALID, "term": "商品。"})


def test_term_with_semicolon_rejected():
    with pytest.raises(ValidationError):
        TerminologyPayload(**{**VALID, "term": "商品；货品"})


def test_primary_collection_required():
    payload = {k: v for k, v in VALID.items() if k != "primary_collection"}
    with pytest.raises(ValidationError):
        TerminologyPayload(**payload)


def test_primary_database_required():
    payload = {k: v for k, v in VALID.items() if k != "primary_database"}
    with pytest.raises(ValidationError):
        TerminologyPayload(**payload)


def test_db_type_required():
    payload = {k: v for k, v in VALID.items() if k != "db_type"}
    with pytest.raises(ValidationError):
        TerminologyPayload(**payload)


def test_db_type_oracle_accepted():
    """oracle 是合法的 db_type，应通过校验."""
    p = TerminologyPayload(**{**VALID, "db_type": "oracle"})
    assert p.db_type == "oracle"


def test_db_type_invalid_value():
    """未支持的类型（如 postgresql）应被拒绝."""
    with pytest.raises(ValidationError):
        TerminologyPayload(**{**VALID, "db_type": "postgresql"})


def test_synonyms_too_long_dropped_not_rejected():
    """超长 synonym 单独跳过，不连累整条 term 入库."""
    p = TerminologyPayload(**{**VALID, "synonyms": ["货品", "货" * 51, "存货"]})
    assert "货品" in p.synonyms and "存货" in p.synonyms
    assert "货" * 51 not in p.synonyms  # 超长项被 drop

    # 英文短语不受 20 字旧限制，≤50 字的英文 synonym 应保留
    p2 = TerminologyPayload(**{**VALID, "synonyms": ["supplementary agreement"]})
    assert "supplementary agreement" in p2.synonyms


def test_synonyms_with_newline_dropped_not_rejected():
    """含换行/句号的 synonym 单独跳过，保留其余合法项."""
    p = TerminologyPayload(**{**VALID, "synonyms": ["货品", "货品\n标", "存货"]})
    assert "货品" in p.synonyms and "存货" in p.synonyms
    assert "货品\n标" not in p.synonyms


def test_synonyms_blank_element_dropped():
    """空白 synonym 静默跳过，保留合法项."""
    p = TerminologyPayload(**{**VALID, "synonyms": ["货品", "  ", "存货"]})
    assert p.synonyms == ["货品", "存货"]


def test_extra_field_forbidden():
    with pytest.raises(ValidationError):
        TerminologyPayload(**{**VALID, "unknown_field": "x"})
