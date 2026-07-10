"""Equivalence strategy: enum set 等价/超集合并.

移植自 canonical_promote.py:_try_enum_set_equivalent.
签名适配 EquivalenceChecker Protocol.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any


def enum_set_checker(
    cands: list, *, namespace_id: int | None = None,
) -> tuple[Any, str] | None:
    """enum (name, db_value) 集合等价或存在超集 → 选最大集.

    返回 (winner, audit_reason):
      - "enum_superset_selected": 已存在某个 candidate 是全集
      - "enum_superset_synthesized": 没有 candidate 是全集, 选 cands[0] 写入并集
    返回 None 表示有真冲突 (同 name 不同 db_value).

    namespace_id 由 _try_rule 统一传入, 本 checker 不使用 (纯结构比较, 无 LLM 调用).
    """
    sets: list[tuple[set[tuple[str, Any]], Any]] = []
    for c in cands:
        v = json.loads(c.candidate_value_json).get("enum_values", [])
        s = {(item.get("name"), item.get("db_value")) for item in v}
        sets.append((s, c))

    # 检查同 name 下 db_value 是否冲突
    name_to_db_value: dict[str, set] = defaultdict(set)
    for s, _ in sets:
        for name, db_value in s:
            if name is not None:
                name_to_db_value[name].add(db_value)
    for _name, vals in name_to_db_value.items():
        if len(vals) > 1:
            return None  # 同 name 多 db_value, 冲突

    # 取并集
    union: set[tuple[str, Any]] = set()
    for s, _ in sets:
        union |= s

    # 选已是全集的 candidate (优先选最大集)
    for s, c in sets:
        if s == union:
            return (c, "enum_superset_selected")

    # 没有任何 candidate 已是全集 → 选第一个并扩为并集
    winner = sets[0][1]
    winner.candidate_value_json = json.dumps(
        {"enum_values": [{"name": n, "db_value": v} for n, v in sorted(union)]},
        ensure_ascii=False,
    )
    return (winner, "enum_superset_synthesized")
