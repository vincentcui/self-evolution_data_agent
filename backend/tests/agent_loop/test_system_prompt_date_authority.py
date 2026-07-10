"""Spec 2026-07-11: system prompt 当前日期注入 + 知识路径权威性约束测试.

覆盖 design.md §6 三层:

6.1  prompt 文本契约 — 含 `当前日期:` 行 / 日期 YYYY-MM-DD 格式 / 知识路径权威性段关键锚点
6.2  local_now 入口一致性 — `当前日期:` 行的日期 == local_now().strftime("%Y-%m-%d");
     守卫 registry.py build_system_prompt 不出现 datetime.now( 裸调
6.3  既有回归 — test_query_anchor_injection.py 的 anchors/critical 渲染路径不受新增段影响

测试原则: 真实 build_system_prompt 调用 (mock settings + 空 namespace), 不 mock LLM/DB.
"""
from __future__ import annotations

import re
import types

from app.config import settings
from app.engine.tools.registry import build_system_prompt
from app.models.base import local_now

# ════════════════════════════════════════════
#  最小 namespace stub (无需 DB, 仅占位 _ = namespace)
#  build_system_prompt 做 _ = namespace, 不访问任何属性,
#  SimpleNamespace 比真实 SQLAlchemy Namespace 更诚实表达意图.
# ════════════════════════════════════════════

_STUB_NS = types.SimpleNamespace(name="test_date_auth", slug="test_date_auth", description="")


# ════════════════════════════════════════════
#  6.1  prompt 文本契约
# ════════════════════════════════════════════


def test_current_date_line_present():
    """build_system_prompt 输出须含「当前日期:」行 (D1 最小形态, 照搬 Claude Code)."""
    prompt = build_system_prompt(settings=settings, namespace=_STUB_NS, anchors=[], critical=[])
    assert "当前日期:" in prompt, "system prompt 必须含「当前日期:」行"


def test_current_date_format_yyyy_mm_dd():
    """「当前日期:」行的日期值须符合 YYYY-MM-DD 格式."""
    prompt = build_system_prompt(settings=settings, namespace=_STUB_NS, anchors=[], critical=[])
    # 提取「当前日期: YYYY-MM-DD」值
    match = re.search(r"当前日期:\s*(\S+)", prompt)
    assert match, "prompt 中未找到「当前日期: <value>」"
    date_str = match.group(1)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str), (
        f"日期值「{date_str}」不符合 YYYY-MM-DD 格式"
    )


def test_knowledge_authority_anchor_must_in_query():
    """权威性段须含「必须落入最终 query」— 防误删核心约束."""
    prompt = build_system_prompt(settings=settings, namespace=_STUB_NS, anchors=[], critical=[])
    assert "必须落入最终 query" in prompt


def test_knowledge_authority_anchor_forbidden():
    """权威性段须含「禁止」— 封死"路径复杂/字段更简单"退路."""
    prompt = build_system_prompt(settings=settings, namespace=_STUB_NS, anchors=[], critical=[])
    assert "禁止" in prompt


def test_knowledge_authority_anchor_opportunistic():
    """权威性段须含「投机取巧」— 反模式点名 (D8 行为锚点)."""
    prompt = build_system_prompt(settings=settings, namespace=_STUB_NS, anchors=[], critical=[])
    assert "投机取巧" in prompt


def test_knowledge_authority_anchor_clarify_with_user():
    """权威性段须含「clarify_with_user」— 召回路径有误时的 approval gate."""
    prompt = build_system_prompt(settings=settings, namespace=_STUB_NS, anchors=[], critical=[])
    assert "clarify_with_user" in prompt


# ════════════════════════════════════════════
#  6.2  local_now 入口一致性
# ════════════════════════════════════════════


def test_current_date_matches_local_now():
    """「当前日期:」行的日期值 == local_now().strftime("%Y-%m-%d") — 时区单一真相源."""
    expected_date = local_now().strftime("%Y-%m-%d")
    prompt = build_system_prompt(settings=settings, namespace=_STUB_NS, anchors=[], critical=[])
    match = re.search(r"当前日期:\s*(\S+)", prompt)
    assert match, "prompt 中未找到「当前日期: <value>」"
    assert match.group(1) == expected_date, (
        f"prompt 日期「{match.group(1)}」与 local_now() 日期「{expected_date}」不一致"
    )


def test_build_system_prompt_no_bare_datetime_now():
    """registry.py 全模块不得出现 datetime.now( 裸调 (非注释行).

    守卫 base.py:16-17 明令: Python 层当前时间唯一入口 = local_now(),
    禁止 datetime.now(tz) 裸调 — 防 P01-T1 时区配置化被绕过.
    注意: 注释中对禁令的说明本身可能含 datetime.now(, 只检查非注释代码行.
    守卫范围: 全模块 (非仅 build_system_prompt 函数体), 防 module-level 裸调逃脱守卫.
    """
    import inspect

    import app.engine.tools.registry as reg_module

    source = inspect.getsource(reg_module)
    # 仅检查非注释代码行 (去掉行首空白后以 # 开头的行是纯注释)
    code_lines = [
        line for line in source.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    code_only = "\n".join(code_lines)
    assert "datetime.now(" not in code_only, (
        "registry.py 含 datetime.now( 裸调 — 违反 base.py:16-17 时区单一真相源约束"
    )


# ════════════════════════════════════════════
#  6.3  既有回归 — 新增段 + current_date 参数不破坏既有 section 渲染
# ════════════════════════════════════════════


def test_empty_bundle_skeleton_intact():
    """空 anchors/critical → 模板骨架保留 (错误消费与循环规避)，section 标题不出现.

    回归: .format() 新增 current_date 参数不破坏既有 placeholder 渲染.
    """
    prompt = build_system_prompt(settings=settings, namespace=_STUB_NS, anchors=[], critical=[])
    # 骨架存活
    assert "错误消费与循环规避" in prompt
    # 空时无 section 标题
    assert "## 关键规则 (critical)" not in prompt
    assert "## 业务术语锚点 (terminology)" not in prompt


def test_critical_section_still_renders():
    """有 critical 规则时 critical_section 正确渲染 — 新增权威性段不遮盖."""
    prompt = build_system_prompt(
        settings=settings,
        namespace=_STUB_NS,
        anchors=[],
        critical=["禁止全表扫描"],
    )
    assert "## 关键规则 (critical)" in prompt
    assert "禁止全表扫描" in prompt
