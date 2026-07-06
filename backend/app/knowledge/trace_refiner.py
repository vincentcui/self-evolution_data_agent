"""Stage 2 抓手 E — agent_traces 批量提炼: LLM 看多 trace → 提案 KE list.

5 类宪章 entry_type (terminology / instance_alias / example / rule / route_hint).
输出 list[ProposedKE], 调用方 (refine endpoint) 走 save_knowledge 入待审池.

**LLM 投喂三段结构**: trace_summary (N 步骨架) + known_facts (已知禁区) +
inflection_points (agent 试错转折点种子). 替代旧的 trace_excerpt[:5000] 硬截.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from langfuse import observe

from app.engine.json_parser import parse_llm_json
from app.engine.llm import chat_completion
from app.knowledge.trace_compression import summarize_trace_for_llm

log = logging.getLogger(__name__)

ALLOWED_TYPES: frozenset[str] = frozenset({
    "terminology", "instance_alias", "example", "rule", "route_hint",
})


@dataclass
class ProposedKE:
    entry_type: str
    content: str
    payload: dict
    evidence: dict
    source_trace_id: str = ""


_PROMPT = """\
<role>知识库整理助手 — 从 agent 历史 trace 中提炼可复用的业务知识条目.</role>

<goal>
读 N 条 agent loop trace (用户问题 + tool 调用序列), 从 agent 通过试错探索学到的
模式中提炼**可跨次复用**的语义知识, 写入待审池.
</goal>

<input_structure>
每条 trace 含三段已预处理的视图:

1. trace_summary — 按步骨架 (step / tool / target / pipeline_signature /
   rows_returned 等). 包含全部 N 步.
2. known_facts — 该 trace 中 agent 已经"知道"的信息. 此处列出的内容请视为已有,
   不再产对应的 KE 提案:
   - known_schemas: fetch_schema 拿到的 collection → 字段列表
   - schema_known_enum_fields: schema 已标注 enum_values 的字段 (形如
     'c_X.fieldName'). 这些字段的枚举映射已在 schema 层, 不要产 rule 复述.
   - recalled_kes: lookup_knowledge 已召回的 KE (entry_id + 摘要).
   - system_prompt_critical_rules: agent 主 system prompt 已注入的硬规则.
3. inflection_points — agent 试错转折点 (target_switch / pipeline_complexity_jump /
   retry_after_empty). **这是提炼 route_hint / join_pattern rule 的主要种子**.
</input_structure>

<entry_types>
- terminology — 业务术语映射. 用户用 X 词指代 → 实际落在某集合的某字段.
  例: "活跃用户" → users 集合, last_login_at 字段.
- instance_alias — 口语别名映射到某条具体记录.
  例: "我们的旗舰商品" → product_id="p_007".
- example — 问题-查询成功对模板. 同类问题再来时可参考.
- rule — 业务约束 / 关联模式 / 隐式过滤. 重点两子类:
  (a) filter_default: agent 在 $match 加了某字段但用户原句没要求 — 隐式约定
  (b) join_pattern: 集合间用什么字段关联 (例: A.x 关联 B.y, 而非 B.id)
- route_hint — 跨集合导航. **一条 route_hint 应携带解决该类问题的完整导航信息**:
  穿透链 (用户口语提到 collection_A, 数据实际在 collection_B 嵌套数组) +
  关联字段 (集合间用哪个 key 关联) + 嵌套位置 (字段在第几层) +
  避坑指南 (哪些路径试过返空). 不要把同一类问题的导航信息拆成多条 KE.
</entry_types>

<output_format>
严格 JSON, 无 markdown 围栏:
{"proposed": [
  {"entry_type": "...", "content": "...", "payload": {...},
   "evidence": {"trace_ids": ["..."], "reasoning": "..."},
   "source_trace_id": "..."},
  ...
]}
</output_format>

<payload_schema_per_type>
仅产以下字段:

terminology:
  - term (str, ≤30 字, 单一业务名词, 不含句号/换行)
  - primary_collection (str, 该术语主要落在哪个集合)
  - synonyms (list[str], 可空)
  - primary_field (str, 可空, 字段路径, 支持嵌套如 "groups.resources.resourceType")

instance_alias:
  - alias (str, ≤50 字, 用户口语别名)
  - canonical_name (str, 记录的全名供审核者识别)
  - target_id (str, 记录的 _id 或唯一键值)
  - id_field (str, 默认 "_id")

example:
  - question_pattern (str, 可复用语义骨架, 用于向量检索匹配类似问题)
  - collections (list[str], 有序 db.collection 链, 描述此问题涉及的所有表/集合)
  - join_keys (list[dict], 跨表连接键 [{"from":"orders.user_id","to":"users.id"}])
  - final_query_plan (dict, 统一查询计划, steps 内 db_type 按数据库类型多态, operation:sql|aggregate|filter)
  - result_summary (str, 可空, ≤160 字, 自然语言描述过滤/关联/聚合模式)

rule:
  - rule_text (str, 一句话规则描述, 含字段路径)
  - rule_kind (Literal: "business_constraint" | "filter_default" | "join_pattern")
  - priority (int, 默认 0)

route_hint:
  - question_pattern (str, 何种问题适用此路由)
  - reason (str, ≤160 字, 引用具体 step 说明为何走此路径)
</payload_schema_per_type>

<constraints>
1. evidence.reasoning 必须**引用 trace_summary 中具体 step 序号 + 字段或 pipeline
   片段**, 例: "step 13 在 c_X 上用 id 关联返空, step 16 改 docId 才得 12 行".
2. **inflection_points 是核心素材**: 这些转折点反映 agent 试错学到的知识,
   是产 route_hint / join_pattern rule 的优先来源.
3. **按召回单元聚合**: 召回名额有限, 多条细碎 KE 会互相挤占召回位. 多个
   inflection_points 服务同一类用户问题时, 聚合产**一条富信息 KE** (含完整
   路径 + 关联字段 + 嵌套位置 + 避坑指南). 自检: 想象未来用户问该
   question_pattern, 单这一条 KE 是否就够指导 query 构造? 不够才拆分.
4. **仅产 known_facts 之外的新知识**: 提案涉及的字段/规则/KE 必须既未列在
   schema_known_enum_fields, 也未被 system_prompt_critical_rules 覆盖, 也不重复
   recalled_kes 已有的内容. 三个条件全部满足才产.
5. 一条 trace 中如无可提炼的稳定知识, 该条 trace 不要产任何提案 (宁可漏不可凑).
6. 每条提案 source_trace_id 必填.
7. 每条 content ≤200 字.
</constraints>

<examples>
合法 route_hint (一条富信息 KE 含路径+关联+嵌套+避坑, 通用电商域):
{"entry_type": "route_hint",
 "content": "订单维度统计商品类别需经 orders→items.sku→products 路径, 类别字段在 products.categories[] 数组内",
 "payload": {
   "question_pattern": "统计订单中各商品类别数量/占比",
   "reason": "trace t1: (1) 路径穿透 — step2 在 orders 上 $group by category 返 0 行 (orders 无 category), step5 走 orders.items → products 才有数据; (2) 关联字段 — orders.items[].sku ↔ products.sku, 非 products.id; (3) 嵌套位置 — 类别在 products.categories[] 数组, 需 $unwind; (4) 避坑 — step3 用 products.id 关联返空, 改 sku 才得 90 行"},
 "evidence": {"trace_ids": ["t1"],
   "reasoning": "trace t1 inflection_points 含 target_switch orders→products + retry_after_empty step3→step5 + pipeline_complexity_jump step5 stages 5→8"},
 "source_trace_id": "t1"}

合法 rule (join_pattern, 独立基础规则):
{"entry_type": "rule",
 "content": "users 与 orders 用 users.id ↔ orders.user_id 关联, 不用 orders.id",
 "payload": {
   "rule_text": "users → orders 关联走 orders.user_id (非 orders.id)",
   "rule_kind": "join_pattern", "priority": 10},
 "evidence": {"trace_ids": ["t2"],
   "reasoning": "trace t2 step 3 用 orders.id 关联返空, step 5 改 orders.user_id 才得数据"},
 "source_trace_id": "t2"}

合法 example:
{"entry_type": "example",
 "content": "按状态分组统计订单数",
 "payload": {"question_pattern": "查看各订单状态的数量分布",
             "collections": ["shop.orders"],
             "join_keys": [],
             "final_query_plan": {"steps": [{"db_type":"mongodb","database":"shop","collection":"orders","operation":"aggregate","query":{"pipeline":[{"$group":{"_id":"$status","count":{"$sum":1}}}]}}]},
             "result_summary": "在 orders 上按 status 字段 $group + $sum:1"},
 "evidence": {"trace_ids": ["t3"],
   "reasoning": "trace t3 step 1 一次性在 orders 上 $group by status 即得结果, pipeline 简单可复用"},
 "source_trace_id": "t3"}
</examples>

<escape_valve>
如果 N 条 trace 都是一次性具体 ID 查询、或所有可提炼内容已在 known_facts 中,
返回 {"proposed": []}. 宁可空也不要凑.
</escape_valve>"""


@observe(name="trace_refiner.refine", as_type="chain")
def refine_traces(
    traces: list[dict],
    critical_rules: list[str] | None = None,
) -> list[ProposedKE]:
    """traces 格式: [{trace_id, user_query, trace_json, reflection_log_json}, ...]

    Args:
        traces: trace 字典列表
        critical_rules: 当前 ns 的 critical KE content list (调用方从
            knowledge_loader._load_layer1_knowledge 取). 注入 known_facts
            让 LLM 不重复总结已属 critical 的过滤规则.
    """
    if not traces:
        return []
    body = []
    for t in traces[:50]:
        compressed = summarize_trace_for_llm(
            t.get("trace_json") or "", critical_rules,
        )
        body.append({
            "trace_id": t.get("trace_id"),
            "user_query": (t.get("user_query") or "")[:500],
            **compressed,
        })
    user_msg = json.dumps(body, ensure_ascii=False)
    try:
        raw = chat_completion(
            [{"role": "system", "content": _PROMPT}, {"role": "user", "content": user_msg}],
            temperature=0.2, max_tokens=4096,  # noqa: hardcode
        )
    except Exception as e:
        log.warning("[trace_refiner] LLM 失败: %s", e)
        return []

    data = parse_llm_json(raw, expect="dict")
    if data is None:
        return []

    out: list[ProposedKE] = []
    for item in data.get("proposed", []):
        try:
            et = item.get("entry_type")
            if et not in ALLOWED_TYPES:
                continue
            out.append(ProposedKE(
                entry_type=et,
                content=str(item.get("content", ""))[:2000],
                payload=item.get("payload") or {},
                evidence=item.get("evidence") or {},
                source_trace_id=str(item.get("source_trace_id") or ""),
            ))
        except Exception:
            continue
    return out
