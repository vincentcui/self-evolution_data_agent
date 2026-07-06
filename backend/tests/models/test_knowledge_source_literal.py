"""KnowledgeSource Literal 与 REPO_REBUILDABLE_SOURCES 常量回归."""
from app.knowledge.trainer_purge import REPO_REBUILDABLE_SOURCES
from app.models.knowledge_entry import KnowledgeSource


def test_knowledge_source_literal_members():
    import typing
    args = typing.get_args(KnowledgeSource)
    assert set(args) == {"manual", "agent_learn", "schema", "code_extract"}


def test_repo_rebuildable_sources():
    assert REPO_REBUILDABLE_SOURCES == frozenset({"code_extract"})
