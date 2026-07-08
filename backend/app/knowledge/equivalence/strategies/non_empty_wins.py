"""Equivalence strategy: non_empty_wins — 一空一非空取非空.

移植自 canonical_promote.py:_try_non_empty_wins.
仅适用于 kind=field_description.
"""

from __future__ import annotations

import json
from typing import Any


def non_empty_wins_checker(
    cands: list, *, namespace_id: int | None = None,
) -> tuple[Any, str] | None:
    """description 一空一非空 → 取非空; 都非空且不同 → None (走下一条 rule).

    都空 → 任取第一个.
    namespace_id 由 _try_rule 统一传入, 本 checker 不使用 (纯结构比较, 无 LLM 调用).
    """
    non_empty = [
        c for c in cands
        if json.loads(c.candidate_value_json).get("description", "").strip()
    ]
    if len(non_empty) == 0:
        return (cands[0], "non_empty_wins")  # 都空, 任取
    if len(non_empty) == 1:
        return (non_empty[0], "non_empty_wins")
    # 多个非空且不同 (hash 已在上层排除相同) → 走下一条 rule
    return None
