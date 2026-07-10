"""Equivalence strategy: sample_values 取并集.

移植自 canonical_promote.py:_union_sample_values.
仅适用于 kind=sample_values.
"""

from __future__ import annotations

import json
from typing import Any


def sample_values_union_checker(
    cands: list, *, namespace_id: int | None = None,
) -> tuple[Any, str] | None:
    """sample_values 取并集, 选 cands[0] 写入合并结果.

    namespace_id 由 _try_rule 统一传入, 本 checker 不使用 (纯结构比较, 无 LLM 调用).
    """
    seen: set[str] = set()
    union: list[Any] = []
    for c in cands:
        for v in json.loads(c.candidate_value_json).get("sample_values", []):
            key = json.dumps(v, sort_keys=True)
            if key not in seen:
                seen.add(key)
                union.append(v)

    first = cands[0]
    first.candidate_value_json = json.dumps(
        {"sample_values": union}, ensure_ascii=False
    )
    return (first, "sample_values_union")
