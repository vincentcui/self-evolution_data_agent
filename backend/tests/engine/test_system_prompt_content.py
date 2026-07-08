"""system prompt 关键段文本断言 — 防止错误消费引导/lookup 描述被误删."""
from app.config import settings
from app.engine.tools.registry import TOOL_SPECS, build_system_prompt


def test_error_consumption_guidance_present():
    """C4: 错误消费五步引导在 system prompt 中."""
    prompt = build_system_prompt(settings=settings, namespace=None)
    assert "错误消费与循环规避" in prompt
    assert "先读错误信息" in prompt
    assert "再判类别" in prompt
    assert "避免无效重试" in prompt


def test_old_dead_loop_heading_removed():
    """旧标题消失."""
    prompt = build_system_prompt(settings=settings, namespace=None)
    assert "死循环规避" not in prompt


def test_no_db_dialect_in_error_section():
    """错误消费段不泄漏特定数据库方言 (errmsg/Did you mean)."""
    prompt = build_system_prompt(settings=settings, namespace=None)
    idx = prompt.find("错误消费与循环规避")
    assert idx >= 0
    section = prompt[idx:idx + 600]
    assert "errmsg" not in section
    assert "Did you mean" not in section


def test_lookup_description_chained_guidance():
    """C5: 链式引导 — 召回术语→二次查 terminology."""
    spec = next(s for s in TOOL_SPECS if s["name"] == "lookup_knowledge")
    desc = spec["description"]
    assert "再查一次" in desc and "terminology" in desc


def test_lookup_description_positive_triggers():
    """C5: 正向触发 — 查询模板（非 pipeline 模板）. 无旧负向表述."""
    spec = next(s for s in TOOL_SPECS if s["name"] == "lookup_knowledge")
    desc = spec["description"]
    assert "查询模板" in desc
    assert "pipeline 模板" not in desc
    assert "未在锚点覆盖的业务名词/别名" not in desc


def test_lookup_description_terminology_in_types():
    """C5: terminology 在 types 列表中."""
    spec = next(s for s in TOOL_SPECS if s["name"] == "lookup_knowledge")
    desc = spec["description"]
    assert "terminology(业务术语→库/表路由)" in desc


def test_lookup_description_all_five_payloads():
    """C5: 五类 payload 字段描述齐全."""
    spec = next(s for s in TOOL_SPECS if s["name"] == "lookup_knowledge")
    desc = spec["description"]
    assert "terminology {term" in desc
    assert "instance_alias {alias" in desc
    assert "example {question_pattern" in desc
    assert "route_hint {question_pattern" in desc
    assert "rule {rule_text" in desc


def test_lookup_description_field_annotations_preserved():
    """C5: 原版 field-level 中文注解未丢失."""
    spec = next(s for s in TOOL_SPECS if s["name"] == "lookup_knowledge")
    desc = spec["description"]
    assert "question_pattern:语义骨架" in desc
    assert "collections:[表名/集合名]" in desc
    assert "from:源表.字段" in desc and "to:目标表.字段" in desc
    assert "result_summary?" in desc
    assert "reason:路径理由" in desc
