"""Phase 7 契约测试 — instance_alias db_type 全栈同步 (T17/T18/T19/T20).

锁定:
  - trace_refiner._PROMPT instance_alias 段含 db_type + target_database + target_collection
  - CLARIFY_EXTRACT_PROMPT instance_alias 示例含 db_type
  - SYSTEM_PROMPT_TEMPLATE 含 instance_alias 别名直达指导 + 不含死代码 prequery_collection
  - instance_alias_intake docstring 不含过时 prequery_collection 描述
"""

from app.api._clarify_extract_prompt import CLARIFY_EXTRACT_PROMPT
from app.engine.tools.registry import SYSTEM_PROMPT_TEMPLATE
from app.knowledge import instance_alias_intake
from app.knowledge.trace_refiner import _PROMPT


# ── T17: trace_refiner _PROMPT ──

def test_trace_refiner_prompt_instance_alias_has_db_type():
    assert "db_type" in _PROMPT
    assert "- db_type (str, 该记录所在库的数据库类型" in _PROMPT


def test_trace_refiner_prompt_instance_alias_has_target_database_collection():
    assert "- target_database (str, 记录所在数据库名)" in _PROMPT
    assert "- target_collection (str, 记录所在集合/表名)" in _PROMPT


# ── T18: CLARIFY_EXTRACT_PROMPT ──

def test_clarify_extract_prompt_instance_alias_has_db_type():
    assert "db_type" in CLARIFY_EXTRACT_PROMPT
    assert 'db_type="mongodb"' in CLARIFY_EXTRACT_PROMPT


# ── T19: SYSTEM_PROMPT_TEMPLATE ──

def test_system_prompt_has_instance_alias_guidance():
    assert "instance_alias 别名直达" in SYSTEM_PROMPT_TEMPLATE
    assert "execute_query" in SYSTEM_PROMPT_TEMPLATE
    assert "跳过 fetch_schema / inspect_values 探查" in SYSTEM_PROMPT_TEMPLATE


def test_system_prompt_no_prequery_collection():
    assert "prequery_collection" not in SYSTEM_PROMPT_TEMPLATE


# ── T20: instance_alias_intake docstring ──

def test_intake_docstring_no_prequery():
    doc = instance_alias_intake.__doc__ or ""
    assert "prequery_collection" not in doc
    assert "execute_query" in doc
