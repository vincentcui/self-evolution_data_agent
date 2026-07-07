"""Stage 2 抓手 A — 由 LLM 为 rule / route_hint 生成"假设触发问题"用作 ChromaDB 多向量 key.

仅启用类型: rule / route_hint (Stage 2 决策 D3).
失败容错: LLM 异常返空数组, upsert 退化为单向量入库.

Phase 3 升级: HQItem (q + covered_path) + is_valid_covered_path 严格连续子序列校验.
"""
from __future__ import annotations

import logging

from langfuse import observe
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from app.config import settings
from app.engine.json_parser import parse_llm_json
from app.engine.llm import chat_completion

log = logging.getLogger(__name__)

ENABLED_ENTRY_TYPES: frozenset[str] = frozenset({"rule", "route_hint"})


# ════════════════════════════════════════════
#  Phase 3: HQItem schema + 严格判别
# ════════════════════════════════════════════

class HQItem(BaseModel):
    """LLM 产出的单条 HQ (含路径自报). 长度上限走 IS_* env var."""

    model_config = ConfigDict(extra="forbid")

    q: str
    covered_path: list[str]

    @field_validator("q")
    @classmethod
    def _q_length(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("q 不能为空")
        max_len = settings.hq_question_max_len
        if len(v) > max_len:
            raise ValueError(f"q 超长 (≤{max_len}, 当前 {len(v)})")
        return v.strip()

    @field_validator("covered_path")
    @classmethod
    def _path_length(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("covered_path 不能为空")
        max_len = settings.hq_covered_path_max
        if len(v) > max_len:
            raise ValueError(f"covered_path 超长 (≤{max_len}, 当前 {len(v)})")
        return v


def is_valid_covered_path(covered: list[str], route: list[str]) -> bool:
    """连续子序列严格方向判别.

    覆盖路径必须是 route 的一段连续子串, 长度 ≥ 1, 方向与 route 一致.
    跳过中间环节或方向反转均不合法.
    """
    if not covered or not route or len(covered) > len(route):
        return False
    n, m = len(route), len(covered)
    for start in range(n - m + 1):
        if route[start: start + m] == covered:
            return True
    return False


def text_includes_collections(
    q: str,
    collections: list[str],
    *,
    mode: str = "lenient",
    terminology_lookup: dict[str, list[str]] | None = None,
) -> bool:
    """二级校验: q 文本必须语义触及 collections 中每个 collection.

    mode:
      - "strict": collection 名必须以子串形式出现在 q
      - "lenient": collection 名或其 terminology.term/synonyms 任一出现即可
      - "off": 跳过 (回退到仅做连续子序列校验)

    terminology_lookup: {collection: [term, syn1, syn2, ...]}, 由 caller 注入.
    """
    if mode == "off":
        return True
    if not q or not collections:
        return False
    for c in collections:
        if mode == "strict":
            if c not in q:
                return False
        else:  # lenient
            tokens = [c]
            if terminology_lookup and c in terminology_lookup:
                tokens.extend(terminology_lookup[c])
            if not any(tok and tok in q for tok in tokens):
                return False
    return True


# ════════════════════════════════════════════
#  Phase 3: generate_hq_with_validation
# ════════════════════════════════════════════

def generate_hq_with_validation(
    content: str,
    *,
    entry_type: str,
    route_collection_path: list[str] | None,
    terminology_lookup: dict[str, list[str]] | None = None,
    n: int | None = None,
    namespace_id: int | None = None,
) -> list[str]:
    """产 HQ + 路径校验. 不通过的丢弃, 不重生.

    - rule 类型: 没有 collection_path 概念, 跳过校验, 直接返 LLM 产的 q 列表
    - route_hint 类型: 有 route_collection_path 时严格校验; 缺时 fallback 到不校验
    - 其他类型: 返空列表
    """
    if entry_type not in ENABLED_ENTRY_TYPES:
        return []

    raw_items = _call_llm_for_hq_items(content, entry_type, n, namespace_id=namespace_id)

    if entry_type == "rule" or not route_collection_path:
        if entry_type == "route_hint" and not route_collection_path:
            log.warning("[hq] route_hint 缺 collection_path, 跳过校验直接返 LLM 输出")
        return [item.q for item in raw_items]

    # route_hint with valid route — 严格校验
    valid: list[str] = []
    for item in raw_items:
        if not is_valid_covered_path(item.covered_path, route_collection_path):
            log.info("[hq] 拒: covered_path 非连续子串 q=%r", item.q[:40])
            continue
        if not text_includes_collections(
            item.q, item.covered_path,
            mode=settings.hq_text_validation_mode,
            terminology_lookup=terminology_lookup,
        ):
            log.info("[hq] 拒: q 文本未含 covered collections q=%r", item.q[:40])
            continue
        valid.append(item.q)

    log.info(
        "[hq] route_hint LLM 产 %d 条, 校验通过 %d 条, 路径=%s",
        len(raw_items), len(valid), route_collection_path,
    )
    return valid


def _call_llm_for_hq_items(
    content: str, entry_type: str, n: int | None,
    namespace_id: int | None = None,
) -> list[HQItem]:
    """LLM 调用 + Pydantic 解析. 失败返空."""
    cap = n if n is not None else settings.hypothetical_queries_per_entry
    prompt = _build_hq_prompt_with_schema(content, entry_type, cap)
    try:
        raw = chat_completion(
            [{"role": "user", "content": prompt}],
            temperature=settings.hypothetical_queries_llm_temperature,
            max_tokens=settings.hypothetical_queries_llm_max_tokens,
            namespace_id=namespace_id,
        )
    except Exception as e:
        log.warning("[hq] LLM 失败, 退化空列表: %s", e)
        return []

    parsed = parse_llm_json(raw, expect="list")
    if not isinstance(parsed, list):
        # fallback: 尝试 dict 格式 {"queries": [...]}
        if isinstance(parsed, dict):
            parsed = parsed.get("queries") or parsed.get("items") or []
        else:
            log.warning("[hq] JSON 解析失败 raw=%s...", (raw or "")[:200])
            return []

    items: list[HQItem] = []
    for p in parsed:
        if not isinstance(p, dict):
            continue
        # 兼容旧格式: 如果 LLM 返纯字符串列表, 包装为 HQItem
        if "q" not in p and isinstance(p, str):
            continue
        try:
            items.append(HQItem(**p))
        except (ValidationError, TypeError) as e:
            log.debug("[hq] HQItem 校验失败: %s", e)
    return items


def _build_hq_prompt_with_schema(
    content: str, entry_type: str, n: int,
) -> str:
    """Phase 3 prompt: 按 prompt-engineering-2026 D1-D8 标准, 要求 LLM 自报 covered_path."""
    max_len = settings.hq_question_max_len
    # route_or_null 由 caller 在 content 中已隐含 (KE.content 描述路由),
    # 但 collection_path 信息不在 prompt 中暴露 — 由后端校验兜底.
    return f'''\
<role>
你是一名问题改写助手, 为业务知识条目生成自然语言查询变体, 用于向量召回.
</role>

<goal>
读 1 条业务知识 (entry_type={entry_type}), 生成至多 {n} 条用户可能的真实问题.
每条问题必须能触发该知识的实际业务路径, 不能误导.
</goal>

<constraints>
- 通用电商域举例 (用户/订单/商品), 不锚定具体行业
- 每条 q 文本 ≤ {max_len} 字
- 严格 JSON 输出, 不含 markdown 围栏
- covered_path 必须是 KE.collection_path 的连续子串, 保留原方向
  - 合法 (route=[a, b, c, d] 时):
    * [a, b] — 从头 2 段
    * [b, c] — 中段 2 段
    * [b, c, d] — 中段到尾
    * [a, b, c, d] — 全路径
  - 非法:
    * [a, c] — 跳过 b
    * [a, b, d] — 跳过 c
    * [d, c, b, a] — 方向反
- q 文本必须语义触及 covered_path 中每个 collection 的业务概念
- 如果无法生成符合上述所有约束的合法 HQ, 返回空数组 [] 即可
</constraints>

<input>
entry_type: {entry_type}
content: {content}
</input>

<output_format>
严格 JSON 数组, 每条 shape:
{{"q": "<问题文本>", "covered_path": ["<col1>", "<col2>", ...]}}

无合法条目返回 []
</output_format>

<examples>
example 1 (route_hint, route=[users, orders, items]):
[
  {{"q": "查询用户某段时间内购买的商品列表", "covered_path": ["users", "orders", "items"]}},
  {{"q": "用户最近 30 天的订单数", "covered_path": ["users", "orders"]}},
  {{"q": "某订单包含的商品明细", "covered_path": ["orders", "items"]}}
]

example 2 (rule, no route):
[
  {{"q": "活跃用户的定义是什么", "covered_path": ["users"]}}
]

example 3 (无合法条目):
[]
</examples>'''

_PROMPT_TEMPLATE = """\
你是一名分析师。下面给你一条业务{kind}, 请列出 {n} 个用户在自然语言提问时,
你会想要让这条知识被召回的提问形态。

要求:
1. 用通用电商业务域举例 (用户 / 订单 / 商品 / 类目 / 金额 / SKU 等), 不要拘泥于具体行业
2. 每条 ≤25 字
3. 互不重复, 视角不同
4. 严格 JSON 输出, 不要 markdown 围栏
5. **如果该条目过于抽象、过短、或缺乏可形成提问的具体语义, 返回 {{"queries": []}}.
   不要强行编造低质量提问 — 召回质量优先于召回数量.**

输入业务条目:
{content}

输出格式:
{{"queries": ["query1", "query2", "query3"]}}"""

_KIND_LABEL = {"rule": "规则", "route_hint": "查询路径偏好"}


@observe(name="hypothetical_queries.generate", as_type="chain")
def generate_hypothetical_queries(
    content: str, entry_type: str, n: int | None = None,
) -> list[str]:
    """rule / route_hint 同步生成 N 条假设触发问题.

    [DEPRECATED Phase 3] 旧版无 covered_path 校验. 保留向后兼容,
    新代码应改调 generate_hq_with_validation 传 route_collection_path.

    返回:
        - 成功: 1-N 条字符串, 每条 ≤50 字 (LLM 边界裁剪)
        - 不支持的 entry_type: 空数组
        - LLM 失败 / JSON 解析失败: 空数组 (调用方退化为单向量)
    """
    return generate_hq_with_validation(
        content, entry_type=entry_type, route_collection_path=None, n=n,
    )
