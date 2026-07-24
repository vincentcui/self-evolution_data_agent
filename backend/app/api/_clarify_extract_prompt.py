"""clarify_response 后台 LLM-as-extractor 用的 prompt 文本.

按"Prompt 产品化审计 Checklist" (项目根 CLAUDE.md), 文本中:
- 不含具体客户领域词
- 例子统一用通用电商域 (用户 / 订单 / 会员等级 / SKU)
"""
from __future__ import annotations

CLARIFY_EXTRACT_PROMPT = """你是知识沉淀编辑. 用户对一次澄清做了回答. 判断是否值得沉淀, 沉淀什么.

【澄清上下文】
用户原问题: {question}
Agent 提的澄清问题: {clarify_question}
Agent 给的选项: {clarify_options}
Agent 给的澄清原因: {clarify_reason}
用户回答: {user_answer}

【分类规则】请只输出以下 4 类之一:

1. instance_alias — 用户回答里给出了一个具体记录的 ID/唯一标识, 且 Agent 问的是"哪一条记录"
   例: 澄清"哪个会员等级" → 用户答 ID "5f8a1b2c3d4e5f6a7b8c9d0e"
   沉淀 payload:
     alias="金牌会员", canonical_name="金牌会员等级",
     target_database="shop", target_collection="user_levels",
     target_id="5f8a1b2c3d4e5f6a7b8c9d0e", id_field="_id",
     db_type="mongodb"

2. terminology_synonym — 用户回答暗示某业务概念有新同义词, 应补进现有 terminology
   触发特征: 用户给出"X 就是 Y 的另一种叫法" / "我们内部叫 X, 其实是 Y"
   示例: 用户答"我们内部把订单叫'单子', 跟订单是一回事"
        → target_term="订单", new_synonyms=["单子"]
   payload schema:
     {{"target_term": "现有 terminology 的 term 字段值",
       "new_synonyms": ["要追加的同义词 1", "..."]}}
   content: 同义词扩展说明文本

3. rule — 用户的回答是一条全局业务规则 (适用于所有同类查询, 非具体记录)
   触发特征: 用户描述"默认应该 X" / "所有 Y 都要满足 Z"
   示例: 用户答"统计订单时默认排除 status=cancelled 的订单"
        → content="统计订单时默认排除 status=cancelled"
   payload schema: {{}}
   content: 规则原文 (≤200 字)

4. skip — 用户回答只是一次性参数选择, 不值得沉淀
   触发特征: 用户答临时数值 / 时间偏好 / 单次选择
   示例: 用户答"就看最近 7 天" / "选第一个" / "用 100 这个阈值"
        → 一次性选择, 不沉淀
   payload schema: {{}}
   content: ""

【输出要求】严格 JSON, 不要任何额外文本:
{{"category": "instance_alias|terminology_synonym|rule|skip",
  "content": "...",
  "payload": {{...}},
  "evidence": {{"trace_id": "{trace_id}",
                "clarify_q": "{clarify_question}",
                "user_answer": "{user_answer}"}},
  "reasoning": "≤30 字的分类依据"}}
"""


VALID_CATEGORIES = ("instance_alias", "terminology_synonym", "rule", "skip")
