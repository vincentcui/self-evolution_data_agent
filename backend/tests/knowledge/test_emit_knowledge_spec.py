# tests/knowledge/test_emit_knowledge_spec.py
def test_emit_knowledge_example_spec_requires_question_query_operation():
    from app.knowledge.extraction_tools import EXTRACTION_TOOL_SPECS
    emit = next(t for t in EXTRACTION_TOOL_SPECS if t["name"] == "emit_knowledge")
    example_schema = next(
        s for s in emit["input_schema"]["properties"]["payload"]["oneOf"]
        if s.get("title") == "example"
    )
    required = example_schema["required"]
    assert "question" in required
    assert "query" in required
    assert "operation" in required
    assert "sql_pattern" not in example_schema["properties"]
    assert "sql_pattern" not in required
    assert example_schema["properties"]["operation"]["enum"] == ["sql", "aggregate", "filter"]


def test_extraction_agent_runtime_gate_accepts_new_example_shape():
    """C1: _emit_knowledge_handler 的运行期闸门 _KNOWLEDGE_PAYLOAD_SCHEMA["example"]
    必须与工具 spec 同步, 否则 agent 产 question+query+operation 被旧 sql_pattern 闸门拒,
    example 提案 100% 被拒 (零产出, 比现状更差). 端到端直调 handler 验证."""
    from app.knowledge.extraction_agent import _KNOWLEDGE_PAYLOAD_SCHEMA
    example_required = _KNOWLEDGE_PAYLOAD_SCHEMA["example"]["required"]
    assert "sql_pattern" not in example_required
    assert set(["question", "query", "operation", "tables"]).issubset(set(example_required))
