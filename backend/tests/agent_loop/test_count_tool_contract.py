"""count mode 措辞契约测试 — 断言 01-design §5 逐字终稿就位 (方向甲 + paradigm 中立).

逐字锚定锁定文本 (非语义近似): 措辞已过 prompt-engineering-2026, 偏离即契约漂移应失败。
措辞为 paradigm 中立 (无 SQL/Mongo 绑定词), 因 execute_query desc 三 paradigm 共享。
"""
from __future__ import annotations

from app.engine.tools.registry import SYSTEM_PROMPT_TEMPLATE, TOOL_SPECS


def _execute_query_desc() -> str:
    spec = next(s for s in TOOL_SPECS if s["name"] == "execute_query")
    return spec["description"]


# ── 站点 A: tool description 逐字 (§5 站点 A) ──
def test_site_a_tool_desc_verbatim():
    desc = _execute_query_desc()
    assert "count=只求总数, 驱动包装返标量 (勿自行聚合)" in desc
    assert "probe=小样本探查" in desc
    assert "只数行" not in desc            # 旧诱导措辞已除
    assert "limit 10" not in desc          # 旧硬编码 10 已除 (probe cap 可配)


# ── 站点 B: SYSTEM_PROMPT step6 逐字 (§5 站点 B) ──
def test_site_b_system_prompt_step6_verbatim():
    assert 'mode="count") — 照常写取数 query, 驱动包装返总数' in SYSTEM_PROMPT_TEMPLATE


# ── 站点 C: 代价控制铁律逐字 + 分组边界 (§5 站点 C) ──
def test_site_c_cost_rule_verbatim():
    assert '照常写取数 query 驱动返标量总数; 要每组明细数量走 mode="single" 自行分组' in SYSTEM_PROMPT_TEMPLATE


# ── paradigm 中立: 三站点措辞无 SQL/Mongo 绑定词 (R2 should_fix 防线) ──
def test_count_wording_paradigm_neutral():
    desc = _execute_query_desc()
    # count 契约行不得含 SQL 味词 (会误导 Mongo pipeline 路径)
    count_seg = desc.split("probe=")[0]  # 截 count 段
    for sql_word in ["SELECT", "COUNT", "GROUP BY"]:
        assert sql_word not in count_seg, f"count 措辞泄漏 SQL 绑定词: {sql_word}"
