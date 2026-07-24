def test_extraction_agent_prompt_instructs_example_native_query():
    """agent prompt 指导 example 产 question + native query body，不产 sql_pattern 字符串。"""
    from app.knowledge.extraction_prompts import load_prompt_or_fallback
    body = load_prompt_or_fallback("extraction-agent-base")
    assert "question" in body
    assert "sql_pattern" not in body
    # query native shape 三形态至少提及
    assert "filter" in body or "pipeline" in body
