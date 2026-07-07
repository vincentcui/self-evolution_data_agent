"""Stage 2 抓手 D — 入库即演化: LLM 判定新条目与近邻的关系.

返回 list[RelatedHit], relation ∈ {equivalent, supplement, conflict, independent}.
independent 不写回 (调用方过滤).

LLM prompt 走通用电商域 (用户/订单/商品), 无客户领域词.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from langfuse import observe

from app.engine.llm import chat_completion

log = logging.getLogger(__name__)

VALID_RELATIONS: frozenset[str] = frozenset({
    "equivalent", "supplement", "conflict", "independent",
})

_PROMPT = """\
你是知识库审核助手. 给定一条新条目, 与若干候选近邻条目, 判定每个近邻与新条目的关系.

关系候选:
- equivalent: 语义等价 — 表述不同但含义相同
- supplement: 互补 — 新条目补全旧条目缺失维度 (同主题但不同维度信息)
- conflict: 冲突 — 描述同一事物但矛盾 (同维度但取值/规则不同)
- independent: 无关

要求:
1. 严格 JSON 输出, 不含 markdown 围栏
2. llm_reason ≤40 字
3. independent 也要返出 (调用方过滤)
4. **如果无法明确判定关系, 返回 relation="independent". 不确定时宁可漏判不可误判 —\
 错误的 conflict 会触发自动覆盖, 错误的 equivalent 会触发自动合并, 都比"无关"风险大.**

通用电商域 4 关系示例:

equivalent (语义等价):
  新: "活跃用户指 30 天内有过登录的用户"
  近邻 [{"id": 1, "content": "活跃用户=过去一个月登录过的用户"}]
  → [{"related_entry_id": 1, "relation": "equivalent", "llm_reason": "30 天 vs 一个月 时间窗等价"}]

supplement (互补):
  新: "VIP 用户享受免邮特权"
  近邻 [{"id": 2, "content": "VIP 用户指本月消费 ≥1000 元的用户"}]
  → [{"related_entry_id": 2, "relation": "supplement",
      "llm_reason": "补充权益维度, 资格定义在旧条目"}]

conflict (冲突):
  新: "VIP 用户指本月消费 ≥1000 元的用户"
  近邻 [{"id": 3, "content": "VIP 用户指过去 30 天消费 ≥1000 元的用户"}]
  → [{"related_entry_id": 3, "relation": "conflict", "llm_reason": "统计周期不同 (本月 vs 30 天)"}]

independent (无关):
  新: "活跃用户=30 天内登录的用户"
  近邻 [{"id": 4, "content": "订单超 24 小时未支付自动取消"}]
  → [{"related_entry_id": 4, "relation": "independent", "llm_reason": "活跃用户与订单超时无关联"}]
"""


@dataclass
class RelatedHit:
    related_entry_id: int
    relation: str
    llm_reason: str


@observe(name="knowledge_relations.detect", as_type="chain")
def detect_relations(
    new_content: str,
    neighbors: list[dict],
    namespace_id: int | None = None,
) -> list[RelatedHit]:
    """新条目 vs 近邻 → list[RelatedHit] (含 independent, 调用方按需过滤).

    neighbors 格式: [{"id": int, "content": str}, ...]
    失败返回空列表 (graceful degradation).
    """
    if not neighbors:
        return []
    body = json.dumps(
        [{"id": n["id"], "content": n["content"][:200]} for n in neighbors],
        ensure_ascii=False,
    )
    user_msg = f"新条目: {new_content[:300]}\n候选近邻:\n{body}"

    try:
        raw = chat_completion(
            [{"role": "system", "content": _PROMPT}, {"role": "user", "content": user_msg}],
            temperature=0.1,
            max_tokens=1024,  # noqa: hardcode
            namespace_id=namespace_id,
        )
    except Exception as e:
        log.warning("[relations] LLM 失败: %s", e)
        return []

    from app.engine.json_parser import parse_llm_json

    data = parse_llm_json(raw, expect="list")
    if data is None:
        log.warning("[relations] JSON 无法解析 raw=%s...", raw[:200])
        return []

    out: list[RelatedHit] = []
    for item in data:
        try:
            rel = item.get("relation", "")
            if rel not in VALID_RELATIONS:
                continue
            out.append(RelatedHit(
                related_entry_id=int(item["related_entry_id"]),
                relation=rel,
                llm_reason=str(item.get("llm_reason", ""))[:40],
            ))
        except (KeyError, ValueError, TypeError):
            continue
    return out
