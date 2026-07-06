"""Bug condition exploration test — 写入/清场标签一致性 (Property 4).

**Validates: Requirements 2.7, 2.8**

═══════════════════════════════════════════════════════════════════════════════
  Property 4: Bug Condition — 写入与清场标签一致
═══════════════════════════════════════════════════════════════════════════════

  对任意术语抽取:
      写入标签 write_tag (`_upsert_terminology_ke` 传给闸门的 source)
      与清场筛选标签 purge_tag (手动刷新清场函数按 `KnowledgeEntry.source ==`
      筛选用的 source) SHALL 相等且均为 "schema":

          write_tag == purge_tag == "schema"

      保证清场无孤儿、能覆盖存量迁移后的条目.

  **CRITICAL**: 本测试在未修复代码上 *预期 FAIL* —— 失败即确认 bug 存在.
  未修复代码:
    - 写入: `_upsert_terminology_ke` → upsert_terminology_with_validation(..., source="code_extract")
    - 清场: `_purge_git_terminology` 按 KnowledgeEntry.source == "code_extract" 筛选
    二者虽一致 (都 "code_extract"), 但均 != "schema" —— 写入标签与"反映 schema 自省抽取
    机制"的真相不符, 清场标签随之耦合于 "code_extract".

  反例形态: write_tag == "code_extract" 且 purge_tag == "code_extract" (均 != "schema").

  方法论: 这是 *静态断言* 测试 —— 直接对两个函数的源码做 AST 提取, 取写入用的
  source 字面量与清场筛选用的 source 字面量, 无需运行抽词链路.

  对 task 9.3 重命名鲁棒: 清场函数当前名 `_purge_git_terminology`, 修复后将改名
  `_purge_schema_terminology`; 本测试按候选名集合解析, 两个名字都能命中.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import app.api.terminology_refresh as refresh_module
import app.knowledge.terminology_refresher as refresher


# ════════════════════════════════════════════════════════════════
#  函数解析 — 兼容 task 9.3 的清场函数重命名
# ════════════════════════════════════════════════════════════════

_PURGE_FN_CANDIDATES = ("_purge_schema_terminology", "_purge_git_terminology")


def _resolve_purge_fn():
    """解析手动刷新清场函数 — 兼容修复前后命名.

    未修复: _purge_git_terminology
    修复后 (task 9.3): _purge_schema_terminology
    """
    for name in _PURGE_FN_CANDIDATES:
        fn = getattr(refresh_module, name, None)
        if fn is not None:
            return name, fn
    raise AssertionError(
        f"未找到清场函数 (候选 {_PURGE_FN_CANDIDATES}) — "
        "无法提取清场筛选标签 purge_tag"
    )


def _parse_fn_ast(fn) -> ast.AST:
    """取函数源码并解析为 AST (dedent 以容忍模块内缩进)."""
    src = textwrap.dedent(inspect.getsource(fn))
    return ast.parse(src)


# ════════════════════════════════════════════════════════════════
#  write_tag 提取 — _upsert_terminology_ke 传给闸门的 source 字面量
# ════════════════════════════════════════════════════════════════


def _extract_write_tag() -> str:
    """从 `_upsert_terminology_ke` 提取写入用的 source 字面量.

    定位 `upsert_terminology_with_validation(..., source=<literal>)` 调用,
    取 `source` 关键字实参的常量值.
    """
    tree = _parse_fn_ast(refresher._upsert_terminology_ke)

    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        callee_name = (
            callee.attr if isinstance(callee, ast.Attribute)
            else callee.id if isinstance(callee, ast.Name)
            else None
        )
        if callee_name != "upsert_terminology_with_validation":
            continue
        for kw in node.keywords:
            if kw.arg == "source" and isinstance(kw.value, ast.Constant):
                found.append(kw.value.value)

    assert found, (
        "未在 _upsert_terminology_ke 中找到 "
        "upsert_terminology_with_validation(..., source=<literal>) 调用 — "
        "无法提取写入标签 write_tag"
    )
    assert len(set(found)) == 1, (
        f"_upsert_terminology_ke 写入用了多个不同 source 字面量: {found}"
    )
    return found[0]


# ════════════════════════════════════════════════════════════════
#  purge_tag 提取 — 清场函数按 KnowledgeEntry.source == <literal> 筛选
# ════════════════════════════════════════════════════════════════


def _extract_purge_tag() -> tuple[str, str]:
    """从清场函数提取清场筛选用的 source 字面量.

    定位 `KnowledgeEntry.source == <literal>` 比较, 取右侧常量值.
    返回 (purge_fn_name, purge_tag).
    """
    purge_name, purge_fn = _resolve_purge_fn()
    tree = _parse_fn_ast(purge_fn)

    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        # 匹配 KnowledgeEntry.source
        if not (
            isinstance(left, ast.Attribute)
            and left.attr == "source"
            and isinstance(left.value, ast.Name)
            and left.value.id == "KnowledgeEntry"
        ):
            continue
        for comparator in node.comparators:
            if isinstance(comparator, ast.Constant):
                found.append(comparator.value)

    assert found, (
        f"未在 {purge_name} 中找到 KnowledgeEntry.source == <literal> 筛选 — "
        "无法提取清场标签 purge_tag"
    )
    assert len(set(found)) == 1, (
        f"{purge_name} 清场用了多个不同 source 字面量: {found}"
    )
    return purge_name, found[0]


# ════════════════════════════════════════════════════════════════
#  Property 4 — write_tag == purge_tag == "schema"
#  (静态断言; 未修复代码上预期 FAIL)
# ════════════════════════════════════════════════════════════════


def test_write_and_purge_source_tags_are_consistent_schema():
    """写入标签与清场筛选标签 SHALL 相等且均为 "schema".

    EXPECTED OUTCOME on unfixed code: FAIL —— 写入=git、清场筛选=git,
    二者一致但均 != "schema", 标签未反映 schema 自省抽取机制.
    """
    write_tag = _extract_write_tag()
    purge_name, purge_tag = _extract_purge_tag()

    # 写入与清场标签必须一致 (否则清场产生孤儿)
    assert write_tag == purge_tag, (
        "写入标签与清场筛选标签不一致 → 清场将产生孤儿条目. "
        f"write_tag={write_tag!r} (来自 _upsert_terminology_ke), "
        f"purge_tag={purge_tag!r} (来自 {purge_name})"
    )

    # 且二者必须均为 schema (反映 schema 自省抽取机制)
    assert write_tag == "schema", (
        "写入标签未反映 schema 自省抽取机制 (应为 'schema'). "
        f"write_tag={write_tag!r}, purge_tag={purge_tag!r} (来自 {purge_name})"
    )
