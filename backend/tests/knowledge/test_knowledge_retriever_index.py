"""Task 6: upsert 统一索引 content — 不再调 build_example_content.

验证 example entry_type 的 upsert 直接索引 content 字段,
不再通过 build_example_content(payload) 拼接.
"""

from unittest.mock import MagicMock, patch


def test_upsert_example_indexes_content_not_build_example_content():
    """example upsert 索引的是 content 本身, 不再调 build_example_content.

    构造 payload 含 nl_paraphrases, 旧逻辑会拼出不同字符串;
    新逻辑应忽略 payload, 直接用 content.
    """
    from app.knowledge.knowledge_retriever import upsert_knowledge_entry

    fake_coll = MagicMock()
    with patch(
        "app.engine.registry.get_knowledge_collection",
        return_value=fake_coll,
    ):
        upsert_knowledge_entry(
            slug="ns",
            entry_id=1,
            content="查在售商品",
            tier="normal",
            namespace_id=1,
            entry_type="example",
            status="canonical",
            payload={
                "question_pattern": "查在售商品",
                "nl_paraphrases": ["有哪些在售商品"],  # 旧逻辑会拼接这个
                "final_query_plan": {"steps": []},
            },
        )

    # 验证 upsert 被调用, documents 就是 content 本身 (不拼 paraphrases)
    fake_coll.upsert.assert_called_once()
    call_kwargs = fake_coll.upsert.call_args.kwargs
    assert call_kwargs["documents"] == ["查在售商品"]
    assert call_kwargs["ids"] == ["ke_1"]


def test_upsert_example_no_knowledge_content_import():
    """确认 knowledge_content 模块不再被 knowledge_retriever 引用."""
    import app.knowledge.knowledge_retriever as mod
    import inspect

    source = inspect.getsource(mod)
    assert "knowledge_content" not in source
    assert "build_example_content" not in source
