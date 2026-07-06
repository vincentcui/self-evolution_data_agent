"""
knowledge_content.py — ChromaDB 索引内容构建 helper

Phase 2 P2.T13: example 类型的向量索引内容由 question + nl_paraphrases 拼接,
增加召回鲁棒性 (用户用各种问法都能命中同 example).
前端展示仍只显示原 question, 拼接形式只服务 ChromaDB 检索.
"""


def build_example_content(payload: dict) -> str:
    """Build ChromaDB-indexed content for example entries.

    Prefers question_pattern (new); falls back to question (old).
    Appends nl_paraphrases for retrieval robustness.
    """
    parts = [payload.get("question_pattern") or payload.get("question", "")]
    parts.extend(payload.get("nl_paraphrases", []))
    return "\n".join(p for p in parts if p)
