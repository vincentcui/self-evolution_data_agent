"""Equivalence strategy: MongoDB 嵌套结构等价.

移植自 mongo_canonical.py:_contents_structurally_equivalent.
签名适配 EquivalenceChecker Protocol: (cands) -> (winner, reason) | None.
"""

from __future__ import annotations

import json
from typing import Any

_MAX_DEPTH = 10

# Java 基本类型 → 装箱类型 (MongoDB 存储等价)
_BOXING_MAP = {
    "int": "Integer", "long": "Long", "double": "Double",
    "float": "Float", "boolean": "Boolean", "short": "Short",
    "byte": "Byte", "char": "Character",
}


def _field_key(f: dict) -> str:
    """返回字段的唯一名称键 — name 优先, field 兜底 (存量旧数据)."""
    return f.get("name") or f.get("field", "")


def _fields_equivalent(fields_a: list[dict], fields_b: list[dict], depth: int = 0) -> bool:
    """递归比较两组 sub_fields 是否结构等价."""
    if depth > _MAX_DEPTH:
        return True

    idx_a = {_field_key(f): f for f in fields_a if _field_key(f)}
    idx_b = {_field_key(f): f for f in fields_b if _field_key(f)}

    if idx_a.keys() != idx_b.keys():
        return False

    for name in idx_a:
        fa, fb = idx_a[name], idx_b[name]
        sa, sb = fa.get("sub_fields"), fb.get("sub_fields")

        if sa and sb:
            if not _fields_equivalent(sa, sb, depth + 1):
                return False
        elif not sa and not sb:
            ta = _BOXING_MAP.get(fa.get("type", ""), fa.get("type", ""))
            tb = _BOXING_MAP.get(fb.get("type", ""), fb.get("type", ""))
            if ta != tb:
                return False
        else:
            return False

    return True


def _enrich_descriptions(target: dict, all_choices: list[dict], depth: int = 0):
    """从所有候选中收集 description, 补全到 target."""
    if depth > _MAX_DEPTH:
        return

    if not target.get("description"):
        for ch in all_choices:
            if ch.get("description"):
                target["description"] = ch["description"]
                break

    target_subs = target.get("sub_fields")
    if not target_subs:
        return

    desc_pool: dict[str, str] = {}
    sub_pool: dict[str, list[dict]] = {}
    for ch in all_choices:
        for sf in ch.get("sub_fields", []):
            fname = _field_key(sf)
            if fname and sf.get("description") and fname not in desc_pool:
                desc_pool[fname] = sf["description"]
            if fname and sf.get("sub_fields"):
                sub_pool.setdefault(fname, []).append(sf)

    for sf in target_subs:
        fname = _field_key(sf)
        if fname and not sf.get("description") and fname in desc_pool:
            sf["description"] = desc_pool[fname]
        if fname and sf.get("sub_fields") and fname in sub_pool:
            _enrich_descriptions(sf, sub_pool[fname], depth + 1)


def mongo_struct_checker(
    cands: list, *, namespace_id: int | None = None,
) -> tuple[Any, str] | None:
    """MongoDB 嵌套结构等价判定.

    比较 candidate_value_json 中的 sub_fields 结构:
    - 全部有 sub_fields 且递归结构等价 → 选最长 content 的 candidate
    - 无 sub_fields 但类型 boxing 等价 → 选装箱类型
    - 否则 → None (不等价)

    namespace_id 由 _try_rule 统一传入, 本 checker 不使用 (纯结构比较, 无 LLM 调用).
    """
    contents: list[tuple[str, dict]] = []
    for c in cands:
        raw = c.candidate_value_json
        obj = json.loads(raw)
        contents.append((raw, obj))

    if len(contents) <= 1:
        return None  # 单候选不需要本 checker

    # 全部都有 sub_fields → 递归比较结构
    all_have_subs = all(obj.get("sub_fields") for _, obj in contents)
    if all_have_subs:
        base_subs = contents[0][1]["sub_fields"]
        if all(_fields_equivalent(base_subs, obj["sub_fields"]) for _, obj in contents[1:]):
            best_idx = max(range(len(contents)), key=lambda i: len(contents[i][0]))
            best_obj = contents[best_idx][1]
            all_objs = [obj for _, obj in contents]
            _enrich_descriptions(best_obj, all_objs)
            # 写回 winner
            winner = cands[best_idx]
            winner.candidate_value_json = json.dumps(best_obj, ensure_ascii=False)
            return (winner, "structural_equivalent")
        # sub_fields 存在但不等价 → 不等价 (不 fallback 到 boxing)
        return None

    # 无 sub_fields 的叶子类型 → 检查装箱/拆箱等价
    types = {obj.get("type", "") for _, obj in contents}
    normalized = {_BOXING_MAP.get(t, t) for t in types}
    if len(normalized) == 1:
        best_idx = max(range(len(contents)), key=lambda i: len(contents[i][1].get("type", "")))
        winner = cands[best_idx]
        return (winner, "structural_equivalent_boxing")

    return None
