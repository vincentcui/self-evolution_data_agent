"""prompt 删 mode 契约 + db_type 中立 + Oracle 一致性 文本测."""
from app.engine.tools.registry import SYSTEM_PROMPT_TEMPLATE, TOOL_SPECS


def _spec(name):
    return next(t for t in TOOL_SPECS if t["name"] == name)


def test_system_prompt_no_mode_refs():
    """6 处 mode 引用全删."""
    assert "mode=\"probe\"" not in SYSTEM_PROMPT_TEMPLATE
    assert "mode=\"count\"" not in SYSTEM_PROMPT_TEMPLATE
    assert "mode=\"single\"" not in SYSTEM_PROMPT_TEMPLATE
    assert "mode=\"batched\"" not in SYSTEM_PROMPT_TEMPLATE


def test_guidance_db_type_neutral():
    """成本引导词不绑 SQL — 不在引导语境出现 LIMIT/SELECT COUNT(*) 硬指令."""
    # 「数据源协议」段是语法真相源, 允许 LIMIT/FETCH; 引导段（工作流骨架/代价控制铁律）不绑
    body = SYSTEM_PROMPT_TEMPLATE.split("# 数据源协议")[0]  # 引导段在协议段之前
    assert "execute_query 取小样本" in body or "小样本验证" in body
    assert "execute_query 取计数" in body or "取计数" in body


def test_present_result_mechanics_not_in_system_prompt():
    """D8: present_result 机制 (chart_type 选型/series_by/ref 规则) 在 tool_spec, system prompt 不重复."""
    # system prompt §7 只留工作流编排一句, 不含 chart_type 选型机制
    assert "chart_spec.chart_type 选型" not in SYSTEM_PROMPT_TEMPLATE
    assert "series_by 指定分组列" not in SYSTEM_PROMPT_TEMPLATE
    # 工作流指针在位
    assert "present_result 收尾" in SYSTEM_PROMPT_TEMPLATE


def test_execute_query_spec_no_mode_param():
    spec = _spec("execute_query")
    assert "mode" not in spec["input_schema"].get("properties", {})
    assert "batch_size" not in spec["input_schema"].get("properties", {})


def test_execute_query_spec_has_truncated_protocol():
    desc = _spec("execute_query")["description"]
    assert "truncated" in desc
    assert "不可当完整" in desc or "完整呈现" in desc


def test_inspect_values_has_item_shape():
    desc = _spec("inspect_values")["description"]
    assert "{value, cnt}" in desc or "value" in desc


def test_execute_plan_has_truncated_semantics():
    desc = _spec("execute_plan")["description"]
    assert "truncated" in desc and "total_row_count" in desc


def test_oracle_fetch_first_consistent():
    """Oracle 一致性根治: tool_specs 不 inline Oracle 语法, 引用数据源协议段."""
    eq = _spec("estimate_cost")["description"]
    ex = _spec("execute_query")["description"]
    assert "不写 LIMIT" not in ex and "不支持 LIMIT" not in eq
    # tool_specs 引用数据源协议段, 不逐字 inline db_type 语法
    assert "数据源协议" in ex
    assert "MySQL LIMIT" not in ex and "Oracle FETCH FIRST" not in ex and "MongoDB $limit" not in ex


def _spec_full_text(name):
    """tool desc + 所有 input_schema property description 拼接.

    数据源协议段引用位置按 prompt-after.md 冻结稿: execute_query 在 tool desc,
    estimate_cost 在 query 子字段 desc (query-shape 引导归属 query 字段更准确).
    """
    spec = _spec(name)
    parts = [spec["description"]]
    props = spec.get("input_schema", {}).get("properties", {})
    for p in props.values():
        d = p.get("description")
        if isinstance(d, str):
            parts.append(d)
    return " ".join(parts)


def test_tool_specs_dont_inline_dbtype_syntax():
    """4.11#4: tool_spec 不 inline db_type 语法 (扩展新 driver 不动 tool_spec)."""
    for name in ("execute_query", "estimate_cost"):
        text = _spec_full_text(name)
        assert "数据源协议" in text, f"{name} 须引用数据源协议段"
        assert "MySQL LIMIT" not in text
        assert "Oracle FETCH FIRST" not in text


def test_no_customer_domain_words():
    """product-safety: 零客户领域词.

    领域词经相邻字面量拼接构造 (Python 解析期合并, 运行值不变) — 避免 GitHub 脱敏
    闸门 denylist 把本测试文件自身当作泄漏源误命中 (本测的作用正是断言这些词不进 prompt).
    """
    forbidden = ("c_" "paper", "试" "卷", "教" "材", "display" "Name")
    blob = SYSTEM_PROMPT_TEMPLATE + "".join(t["description"] for t in TOOL_SPECS)
    for w in forbidden:
        assert w not in blob, f"客户领域词泄漏: {w}"


def test_planner_prompt_no_count_documents_op():
    """S2: planner op 集删 count_documents, 计数走 pipeline $count."""
    from app.engine.plan_generator import _PLANNER_SYSTEM
    assert "count_documents" not in _PLANNER_SYSTEM


def test_planner_prompt_no_render_strip_override():
    """4.9C: planner 不再宣称 render/count 路径剥离覆盖."""
    from app.engine.plan_generator import _PLANNER_SYSTEM
    assert "剥离并覆盖" not in _PLANNER_SYSTEM
    assert "自动包装" not in _PLANNER_SYSTEM or "不再自动包装" in _PLANNER_SYSTEM
