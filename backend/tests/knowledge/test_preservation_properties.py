"""Preservation property tests — confirm baseline behavior BEFORE implementing fix.

**Validates: Requirements 3.3, 3.4, 3.5**

These tests encode the EXISTING correct behavior that must be preserved after the fix:
- _delete_repo_extracted_kes deletes source='code_extract' entries (preservation of existing purge)
- _delete_repo_extracted_kes does NOT delete source NOT in ['code_extract'] (Req 3.5)

All tests should PASS on unfixed code — confirming baseline behavior to preserve.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

from app.models.knowledge_entry import KnowledgeEntry
from app.models.namespace import DataSource, Namespace
from app.models.git_repo import GitRepo


# ════════════════════════════════════════════════════════════════
#  Fixtures — reuse async_session from conftest.py
# ════════════════════════════════════════════════════════════════


@pytest_asyncio.fixture
async def seeded(async_session) -> tuple[int, int, object]:
    """Create namespace + datasource + repo with unique names, return (ns_id, repo_id, session_factory)."""
    uid = uuid.uuid4().hex[:8]
    async with async_session() as db:
        ns = Namespace(name=f"pres_{uid}", slug=f"pres_{uid}", description="preservation test")
        db.add(ns)
        await db.commit()
        await db.refresh(ns)

        ds = DataSource(
            namespace_id=ns.id, db_type="mysql", database="test_db",
            host="localhost", port=3306, username="", password="",
        )
        db.add(ds)
        await db.commit()

        repo = GitRepo(namespace_id=ns.id, url=f"https://example.invalid/pres_{uid}.git")
        db.add(repo)
        await db.commit()
        await db.refresh(repo)
        return ns.id, repo.id, async_session


# ════════════════════════════════════════════════════════════════
#  Strategies
# ════════════════════════════════════════════════════════════════

# Strategy: sources that should NOT be deleted by purge
non_purge_source_st = st.sampled_from(["manual", "schema", "agent_learn"])


# ════════════════════════════════════════════════════════════════
#  Property 2: source='code_extract' entries → _delete_repo_extracted_kes deletes them
#  (preservation of existing purge behavior)
# ════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@settings(
    max_examples=5,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    entry_type=st.sampled_from(["example", "rule", "route_hint", "schema_summary", "terminology"]),
    status=st.sampled_from(["proposed", "canonical", "superseded", "rejected"]),
)
async def test_delete_repo_extracted_kes_deletes_code_extract_entries(
    seeded: tuple[int, int, object],
    entry_type: str, status: str,
):
    """For all KE with source='code_extract': _delete_repo_extracted_kes deletes them.

    **Validates: Preservation of existing purge behavior**
    """
    from app.knowledge.trainer_purge import _delete_repo_extracted_kes

    ns_id, repo_id, session_factory = seeded

    async with session_factory() as db:
        ke = KnowledgeEntry(
            namespace_id=ns_id,
            entry_type=entry_type,
            status=status,
            tier="normal",
            content="test code_extract entry",
            payload="{}",
            source="code_extract",
            repo_id=repo_id,
        )
        db.add(ke)
        await db.flush()
        ke_id = ke.id

        deleted = await _delete_repo_extracted_kes(db, repo_id)
        deleted_ids = [row[0] for row in deleted]

        assert ke_id in deleted_ids, (
            f"_delete_repo_extracted_kes should delete source='code_extract' entry with "
            f"entry_type={entry_type!r}, status={status!r}"
        )
        await db.rollback()


# ════════════════════════════════════════════════════════════════
#  Property 3: source NOT in ['code_extract'] → NOT deleted
#  **Validates: Requirements 3.5**
# ════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    source=non_purge_source_st,
    entry_type=st.sampled_from(["example", "rule", "route_hint", "schema_summary", "terminology"]),
)
async def test_delete_repo_extracted_kes_does_not_delete_non_rebuildable_sources(
    seeded: tuple[int, int, object],
    source: str, entry_type: str,
):
    """For all KE with source NOT in REPO_REBUILDABLE_SOURCES:
    _delete_repo_extracted_kes does NOT delete them.

    **Validates: Requirements 3.5**
    """
    from app.knowledge.trainer_purge import _delete_repo_extracted_kes

    ns_id, repo_id, session_factory = seeded

    async with session_factory() as db:
        ke = KnowledgeEntry(
            namespace_id=ns_id,
            entry_type=entry_type,
            status="canonical",
            tier="normal",
            content=f"test {source} entry",
            payload="{}",
            source=source,
            repo_id=repo_id,
        )
        db.add(ke)
        await db.flush()
        ke_id = ke.id

        deleted = await _delete_repo_extracted_kes(db, repo_id)
        deleted_ids = [row[0] for row in deleted]

        assert ke_id not in deleted_ids, (
            f"_delete_repo_extracted_kes should NOT delete source={source!r} entry, "
            f"but it was deleted"
        )
        await db.rollback()
