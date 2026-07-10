"""验证 mybatis_entries 不再产 route_hint KE (C10/D6).

example 通道由 sql2nl 经 business_examples 恢复 (D3, 见 spec
2026-06-17-agentic-repo-extractor Task 2.1) — mybatis_entries 本身不产 example,
example 由 agent emit_knowledge(entry_type=example) → business_examples 通道写入。

route_hint 第四条自动生产路径 (mybatis 聚合 → _write_route_hints) 已随
spec 2026-07-08-route-hint-demote-and-references-projection Task 10 删除:
route_hint 降为纯人工录入, SELECT SQL 语义改由 example 通道承载。
本文件保留反转断言, 回归保护"mybatis 训练不再产 route_hint"。

原 test_mybatis_entries_do_not_produce_example_ke 已删除: D3 恢复 example 后
"mybatis 不产 example" 的断言语义已不再适用于整体管线 (sql2nl 经独立通道产 example)。
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.knowledge.extraction_writer import extract_and_write_knowledge
from app.models import KnowledgeEntry
from app.models.git_repo import GitRepo
from app.models.namespace import Namespace


@pytest_asyncio.fixture
async def seeded(db_session) -> tuple[int, int]:
    """Create namespace + repo, return (ns_id, repo_id)."""
    ns = Namespace(name="test_ne", slug="test_ne", description="no-example test")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)
    repo = GitRepo(namespace_id=ns.id, url="https://example.invalid/ne.git")
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return ns.id, repo.id


@pytest.mark.asyncio
async def test_mybatis_entries_produce_no_route_hint_ke(db_session, seeded):
    """C10/D6: mybatis 训练不再产 route_hint (第四条自动路径已删)."""
    ns_id, repo_id = seeded
    business_examples = [
        {
            "sql_pattern": "SELECT user_id, real_name FROM t_user WHERE user_id = ?",
            "tables": ["t_user"],
            "question": "查询用户信息",
            "mapper_namespace": "com.example.UserMapper",
        },
    ]

    await extract_and_write_knowledge(
        db_session,
        namespace_id=ns_id,
        repo_id=repo_id,
        business_terms=[],
        business_rules=[],
        business_examples=business_examples,
    )
    await db_session.commit()

    rows = list((await db_session.execute(
        select(KnowledgeEntry).where(
            KnowledgeEntry.namespace_id == ns_id,
            KnowledgeEntry.repo_id == repo_id,
        )
    )).scalars().all())
    route_hint_rows = [k for k in rows if k.entry_type == "route_hint"]
    assert route_hint_rows == [], "route_hint 第四条自动路径应已删除"
