"""count 措辞契约测试 — 断言 kill-mode 后计数引导词逐字终稿就位 (db_type 中立).

逐字锚定锁定文本 (非语义近似): 措辞已过 prompt-engineering-2026 (prompt-after.md 冻结),
偏离即契约漂移应失败。措辞为 db_type 中立 (无 SQL/Mongo 绑定词), execute_query desc 三 db_type 共享。

execute-query-cap-contract (2026-07-11): mode 删后, 计数不再由 driver 包 COUNT —
LLM 自写计数查询 (SQL COUNT(*) / Mongo $count). 引导词从 `mode="count"/"single"`
改为 db_type 中立白话 (`execute_query 取计数 — 勿拉全量`). 本文件锁定新冻结措辞。
"""
from __future__ import annotations

from app.engine.tools.registry import SYSTEM_PROMPT_TEMPLATE, TOOL_SPECS


def _execute_query_desc() -> str:
    spec = next(s for s in TOOL_SPECS if s["name"] == "execute_query")
    return spec["description"]


# ── 站点 A: tool description 逐字 (计数意图 db_type 中立) ──
def test_site_a_tool_desc_verbatim():
    desc = _execute_query_desc()
    assert "行数控制由你在 query 内按 db_type 表达" in desc
    assert "只要总数写计数查询, 勿拉全量再数" in desc
    # 旧 mode 措辞已除
    assert "mode=" not in desc
    assert "驱动包装" not in desc
    assert "probe=小样本探查" not in desc


# ── 站点 B: SYSTEM_PROMPT §6 代价评估 逐字 ──
def test_site_b_system_prompt_step6_verbatim():
    assert "只要行数用 execute_query 取计数 — 勿拉全量." in SYSTEM_PROMPT_TEMPLATE
    # 旧 mode="count" 措辞已除
    assert 'mode="count"' not in SYSTEM_PROMPT_TEMPLATE
    assert 'mode="single"' not in SYSTEM_PROMPT_TEMPLATE


# ── 站点 C: 代价控制铁律 计数 + 分组边界 逐字 ──
def test_site_c_cost_rule_verbatim():
    # 注: SYSTEM_PROMPT_TEMPLATE 用 """...\<换行>""" 续行, 反斜杠+换行被一并消除 → 实际连续串
    assert (
        '用户只问 "个数/占比" → execute_query 取计数 — 勿拉全量; '
        "要每组明细数量走 execute_query 自行分组."
    ) in SYSTEM_PROMPT_TEMPLATE


# ── db_type 中立: 措辞无 SQL/Mongo 绑定词 (会误导另一 paradigm 路径) ──
def test_count_wording_paradigm_neutral():
    desc = _execute_query_desc()
    for sql_word in ["SELECT", "COUNT(", "GROUP BY", "$count", "pipeline"]:
        assert sql_word not in desc, f"execute_query desc 泄漏 db_type 绑定词: {sql_word}"
