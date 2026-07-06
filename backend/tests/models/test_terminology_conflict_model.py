"""Phase 1a Task 1.2 — TerminologyConflict ORM 模型回归.

覆盖:
- 默认 status='open' / resolution_choice 留空 / created_at 自动写入近本地 now
- 解决态字段可填 (status='resolved' + resolution_choice='merge_both')
- ``app.models`` 模块顶层导出别名一致
- FK 级联: namespaces 删除 → conflict CASCADE 清空; git_repos 删除 → candidate_repo_id SET NULL
"""

import json
from datetime import datetime

import pytest
from sqlalchemy import select

from app.models.git_repo import GitRepo
from app.models.namespace import Namespace
from app.models.terminology_conflict import TerminologyConflict


@pytest.mark.asyncio
async def test_create_conflict_default_status_open(async_session, seeded_ns_and_ke):
    ns_id, existing_id = seeded_ns_and_ke
    async with async_session() as db:
        c = TerminologyConflict(
            namespace_id=ns_id,
            existing_entry_id=existing_id,
            candidate_payload=json.dumps({"term": "单子"}),
            candidate_source="code_extract",
        )
        db.add(c)
        await db.commit()
        loaded = (await db.execute(select(TerminologyConflict))).scalar_one()
    assert loaded.status == "open"
    assert loaded.resolution_choice is None
    assert isinstance(loaded.created_at, datetime)
    # LOCAL_NOW 写入应贴近本地 now (≤5 秒漂移); 锁住时区语义防止有人改成 func.now() (UTC)
    assert (datetime.now() - loaded.created_at).total_seconds() < 5


@pytest.mark.asyncio
async def test_resolve_fields_optional(async_session, seeded_ns_and_ke):
    ns_id, existing_id = seeded_ns_and_ke
    async with async_session() as db:
        c = TerminologyConflict(
            namespace_id=ns_id,
            existing_entry_id=existing_id,
            candidate_payload="{}",
            candidate_source="manual",
            status="resolved",
            resolution_choice="merge_both",
        )
        db.add(c)
        await db.commit()
        loaded = (await db.execute(select(TerminologyConflict))).scalar_one()
    assert loaded.status == "resolved"
    assert loaded.resolution_choice == "merge_both"


def test_export_in_models_init():
    from app.models import TerminologyConflict as Exported

    assert Exported is TerminologyConflict


@pytest.mark.asyncio
async def test_namespace_delete_cascades_conflicts(async_session, seeded_ns_and_ke):
    ns_id, existing_id = seeded_ns_and_ke
    async with async_session() as db:
        db.add(
            TerminologyConflict(
                namespace_id=ns_id,
                existing_entry_id=existing_id,
                candidate_payload="{}",
                candidate_source="code_extract",
            )
        )
        await db.commit()
        ns = (await db.execute(select(Namespace).where(Namespace.id == ns_id))).scalar_one()
        await db.delete(ns)
        await db.commit()
        rows = (await db.execute(select(TerminologyConflict))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_repo_delete_sets_candidate_repo_null(async_session, seeded_ns_and_ke):
    ns_id, existing_id = seeded_ns_and_ke
    async with async_session() as db:
        repo = GitRepo(namespace_id=ns_id, url="git@example.com:org/repo.git")
        db.add(repo)
        await db.commit()
        await db.refresh(repo)

        db.add(
            TerminologyConflict(
                namespace_id=ns_id,
                existing_entry_id=existing_id,
                candidate_payload="{}",
                candidate_source="code_extract",
                candidate_repo_id=repo.id,
            )
        )
        await db.commit()

        await db.delete(repo)
        await db.commit()

        loaded = (await db.execute(select(TerminologyConflict))).scalar_one()
    assert loaded.candidate_repo_id is None
