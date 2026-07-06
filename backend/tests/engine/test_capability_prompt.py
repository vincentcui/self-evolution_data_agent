"""系统提示三类能力限制更新 smoke 测试 (task 10.2, R2.3/R2.6).

断言 fetch_schema / estimate_cost 工具描述 + 工作流骨架均引用三类限制 + equivalent_hints,
不再单独引用旧 agg_ops_unsupported (避免同一字段两套矛盾描述)。
"""
from __future__ import annotations

from app.engine.tools.registry import TOOL_SPECS


def _desc(name: str) -> str:
    for spec in TOOL_SPECS:
        if spec.get("name") == name:
            return spec.get("description", "")
    raise AssertionError(f"tool {name} not found")


def test_fetch_schema_desc_three_restrictions():
    """fetch_schema description 已删 server_capabilities 段; 改验 timezone + db_profile (含 caps) 引导.

    plan03 修订 design 决策#11: fetch_schema db_profile 改 schema+caps merge,
    desc 同步提能力限制 (unsupported_ops 等) 供 LLM 生成 pipeline 避坑.
    """
    d = _desc("fetch_schema")
    # 新字段引导存在
    assert "timezone" in d
    assert "db_profile" in d
    # caps 子句随 schema+caps merge 进 desc (plan03 修订 design 决策#11)
    assert "unsupported_ops" in d
    assert "unsupported_stage_variants" in d
    assert "equivalent_hints" in d
    # server_capabilities 旧字段名已删 — 不再引导 LLM 用
    assert "server_capabilities" not in d


def test_estimate_cost_desc_three_restrictions():
    d = _desc("estimate_cost")
    assert "unsupported_stage_variants" in d
    assert "syntax_constraints" in d
    assert "equivalent_hints" in d
    # plan03 Task5: estimate_cost description 改 db_profile (含 timezone+caps),
    # 删 server_capabilities 段
    assert "timezone" in d
    assert "db_profile" in d
    assert "server_capabilities" not in d


def test_system_prompt_step8_uses_db_profile_not_server_capabilities():
    """plan03 Task5: SYSTEM_PROMPT 步骤8 提 db_profile 里的能力限制, 不提 server_capabilities."""
    from app.engine.tools.registry import build_system_prompt

    class _S:
        query_cost_single_layer_limit = 50_000
        query_cost_total_limit = 5_000_000
        agent_reflection_enabled = False

    class _Ns:
        id = 1
        slug = "t"

    prompt = build_system_prompt(settings=_S(), namespace=_Ns())
    assert "db_profile 里的能力限制" in prompt
    assert "server_capabilities" not in prompt


def test_no_standalone_agg_ops_unsupported_in_tool_descs():
    # agg_ops_unsupported 兼容键保留于数据, 但提示文案不再单独引用它
    for name in ("fetch_schema", "estimate_cost"):
        assert "agg_ops_unsupported" not in _desc(name)


def test_b2_dbref_format_note_in_system_prompt():
    """B2: base 系统提示含中立 DBRef 格式说明 ($ref/$id_str), 不绑定 flavor."""
    from app.engine.tools.registry import build_system_prompt

    class _S:
        query_cost_single_layer_limit = 50_000
        query_cost_total_limit = 5_000_000
        agent_reflection_enabled = False

    class _Ns:
        id = 1
        slug = "t"

    prompt = build_system_prompt(settings=_S(), namespace=_Ns())
    assert "$id_str" in prompt
    assert "DBRef" in prompt
    # 中立: 不在 base 里写死多步路径 / 不出现 documentdb
    assert "documentdb" not in prompt.lower()


def test_b1_dbref_multistep_hint_in_documentdb_profile():
    """B1: DocumentDB profile 的 $lookup.let_pipeline hint 含多步 DBRef 关联指引."""
    from app.engine.drivers.mongo_flavor import compute_capabilities

    caps = compute_capabilities("documentdb", "5.0.0")
    hints = {h["restriction"]: h["suggestion"] for h in caps["equivalent_hints"]}
    sug = hints["$lookup.let_pipeline"]
    assert "$id_str" in sug
    assert "多步" in sug
    # 不写死字段名, 用中性"目标集合的关联字段"
    assert "docId" not in sug
