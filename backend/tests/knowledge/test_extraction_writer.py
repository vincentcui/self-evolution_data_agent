"""Tests for extraction_writer — write_canonical_candidates_from_parse + extract_and_write_knowledge.

Validates:
- JPA entities produce table_description + field_description candidates
- Enum classes produce enum_values candidates
- Relationships produce relationship candidates
- business_examples (sql2nl) produce example KE (D3); route_hint 自动生产路径已删 (C10/D6)
- business_rules produce rule KE
- Concurrent writes to same target do not raise UniqueViolationError
"""
import asyncio
import json
import os

import pytest
import pytest_asyncio
from sqlalchemy import delete, event, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.knowledge.extraction_writer import (
    extract_and_write_knowledge,
    write_canonical_candidates_from_parse,
)
from app.models import KnowledgeEntry, SchemaCanonicalCandidate
from app.models.base import Base
from app.models.git_repo import GitRepo
from app.models.namespace import DataSource, Namespace
from app.models.schema_canonical_audit_log import SchemaCanonicalAuditLog

TEST_DATABASE_URL = os.environ.get(
    "IS_TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/self_evolution_data_agent_test",
)


@pytest_asyncio.fixture
async def db_engine() -> AsyncEngine:
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_timezone(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("SET timezone = 'Asia/Shanghai'")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncSession:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture(autouse=True)
def _patch_writer_session(db_engine, monkeypatch):
    """writer 内部用 app.db.metadata.async_session (指向 dev DB); 测试库与 dev 库
    不同 → 写入 candidate 撞 FK。绑定到测试 engine 的 sessionmaker, 使写入落测试库。"""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.knowledge.extraction_writer.async_session", factory)
    monkeypatch.setattr("app.knowledge.canonical_candidate.async_session", factory, raising=False)


@pytest_asyncio.fixture
async def seeded(db_session: AsyncSession) -> tuple[int, int]:
    """Create namespace + datasource + repo, return (ns_id, repo_id)."""
    ns = Namespace(name="test_ew", slug="test_ew", description="extraction writer test")
    db_session.add(ns)
    await db_session.commit()
    await db_session.refresh(ns)

    ds = DataSource(
        namespace_id=ns.id, db_type="mysql", database="test_db",
        host="localhost", port=3306, username="", password="",
    )
    db_session.add(ds)
    await db_session.commit()

    repo = GitRepo(namespace_id=ns.id, url="https://example.invalid/ew.git")
    db_session.add(repo)
    await db_session.commit()
    await db_session.refresh(repo)
    return ns.id, repo.id


@pytest_asyncio.fixture(autouse=True)
async def cleanup(db_session: AsyncSession, seeded):
    """Cleanup candidates and audit logs after each test."""
    ns_id, _ = seeded
    yield
    await db_session.execute(
        delete(SchemaCanonicalAuditLog).where(
            SchemaCanonicalAuditLog.namespace_id == ns_id,
        )
    )
    await db_session.execute(
        delete(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.namespace_id == ns_id,
        )
    )
    await db_session.execute(
        delete(KnowledgeEntry).where(
            KnowledgeEntry.namespace_id == ns_id,
        )
    )
    await db_session.execute(
        delete(DataSource).where(DataSource.namespace_id == ns_id)
    )
    await db_session.execute(
        delete(Namespace).where(Namespace.id == ns_id)
    )
    await db_session.commit()


# ════════════════════════════════════════════════════════════════
#  write_canonical_candidates_from_parse
# ════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_write_candidates_from_jpa_entities(db_session, seeded):
    ns_id, repo_id = seeded

    jpa_entities = [{
        "table_name": "t_order",
        "database": "test_db",
        "description": "订单表",
        "source_file": "Order.java",
        "fields": [
            {"column": "id", "description": "主键", "type": "Long"},
            {"column": "status", "description": "订单状态", "type": "OrderStatus",
             "enum_values": [{"name": "CREATED", "db_value": 1}]},
            {"column": "amount", "description": "", "type": "BigDecimal"},
        ],
    }]

    total = await write_canonical_candidates_from_parse(
        namespace_id=ns_id, repo_id=repo_id,
        jpa_entities=jpa_entities,
        mongo_documents=[], enum_classes=[],
        where_evidence=[],
    )

    # table_description + 2 field_description (amount has empty desc → skipped) + 1 enum_values
    assert total == 4

    rows = (await db_session.execute(
        select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.namespace_id == ns_id
        )
    )).scalars().all()
    assert len(rows) == 4

    kinds = sorted(r.candidate_kind for r in rows)
    assert kinds == ["enum_values", "field_description", "field_description", "table_description"]

    # Verify table_description content
    table_desc = next(r for r in rows if r.candidate_kind == "table_description")
    assert json.loads(table_desc.candidate_value_json)["description"] == "订单表"
    assert table_desc.target == "t_order"


@pytest.mark.asyncio
async def test_write_candidates_from_enum_classes(db_session, seeded):
    ns_id, repo_id = seeded

    enum_classes = [{
        "enum_class": "OrderStatus",
        "fully_qualified_name": "com.example.OrderStatus",
        "linked_table": "t_order",
        "linked_field": "status",
        "db_type": "mysql",
        "database": "test_db",
        "source_file": "OrderStatus.java",
        "values": [
            {"name": "CREATED", "db_value": 1, "description": "已创建"},
            {"name": "PAID", "db_value": 2, "description": "已支付"},
        ],
    }]

    total = await write_canonical_candidates_from_parse(
        namespace_id=ns_id, repo_id=repo_id,
        jpa_entities=[], mongo_documents=[],
        enum_classes=enum_classes,
        where_evidence=[],
    )

    assert total == 1

    rows = (await db_session.execute(
        select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.namespace_id == ns_id
        )
    )).scalars().all()
    assert len(rows) == 1

    cand = rows[0]
    assert cand.candidate_kind == "enum_values"
    assert cand.target == "t_order"
    assert cand.field_path == "status"
    val = json.loads(cand.candidate_value_json)
    assert len(val["enum_values"]) == 2
    assert val["enum_values"][0]["name"] == "CREATED"


@pytest.mark.asyncio
async def test_write_candidates_from_relationships(db_session, seeded):
    """relationship candidate 由 entity writer 内联产生 — 与 field 共享 db_type/database/gate."""
    ns_id, repo_id = seeded

    jpa_entities = [{
        "table_name": "t_order_item",
        "table": "t_order_item",
        "database": "test_db",
        "source_file": "OrderItem.java",
        "fields": [],
        "relations": [{
            "from_target": "t_order_item",
            "from_field": "order_id",
            "to_target": "t_order",
            "to_field": "id",
            "relation_type": "many_to_one",
        }],
    }]

    total = await write_canonical_candidates_from_parse(
        namespace_id=ns_id, repo_id=repo_id,
        jpa_entities=jpa_entities, mongo_documents=[],
        enum_classes=[], where_evidence=[],
    )

    assert total == 1

    rows = (await db_session.execute(
        select(SchemaCanonicalCandidate).where(
            SchemaCanonicalCandidate.namespace_id == ns_id
        )
    )).scalars().all()
    assert len(rows) == 1

    cand = rows[0]
    assert cand.candidate_kind == "relationship"
    assert cand.target == "t_order_item"
    assert cand.db_type == "mysql"
    assert cand.database == "test_db"
    val = json.loads(cand.candidate_value_json)
    assert val["to_target"] == "t_order"
    assert val["relation_type"] == "many_to_one"


# ════════════════════════════════════════════════════════════════
#  Concurrent write — 验证并发安全
# ════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_concurrent_write_same_target_no_error(db_session, seeded):
    """两个并发 writer 写同一 target 的同一 description, 不应报错."""
    ns_id, repo_id = seeded

    # 创建第二个 repo
    repo2 = GitRepo(namespace_id=ns_id, url="https://example.invalid/ew2.git")
    db_session.add(repo2)
    await db_session.commit()
    await db_session.refresh(repo2)

    doc = {
        "collection": "c_tag_mapper",
        "database": "test_db",
        "description": "标签映射关系记录",
        "fields": [{"field": "tag_id", "description": "标签ID"}],
    }

    # 模拟两个 worker 并发写入同一 document
    results = await asyncio.gather(
        write_canonical_candidates_from_parse(
            namespace_id=ns_id, repo_id=repo_id,
            jpa_entities=[], mongo_documents=[doc],
            enum_classes=[], where_evidence=[],
        ),
        write_canonical_candidates_from_parse(
            namespace_id=ns_id, repo_id=repo2.id,
            jpa_entities=[], mongo_documents=[doc],
            enum_classes=[], where_evidence=[],
        ),
    )

    # 两个都应成功, 不抛异常
    assert results[0] >= 1
    assert results[1] >= 1

    # DB 中 table_description 只有一行 (dedup by value_hash)
    count = (await db_session.execute(
        select(func.count(SchemaCanonicalCandidate.id)).where(
            SchemaCanonicalCandidate.namespace_id == ns_id,
            SchemaCanonicalCandidate.target == "c_tag_mapper",
            SchemaCanonicalCandidate.candidate_kind == "table_description",
        )
    )).scalar_one()
    assert count == 1


# ════════════════════════════════════════════════════════════════
#  extract_and_write_knowledge — rule / route_hint / terminology / example 出口
#  (example 出口由 sql2nl business_examples 恢复, D3 — 2026-06-17-agentic-repo-extractor)
# ════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_extract_and_write_knowledge_creates_rule_ke(db_session, seeded):
    ns_id, repo_id = seeded
    # Task 1b: coll_to_db 反查表 — 裸名转 CollectionRef
    coll_to_db = {"t_order": ("mysql", "test_db")}

    business_rules = [{
        "rule_text": "查询订单时默认排除已删除记录: is_deleted=0",
        "applies_to_collections": ["t_order"],
        "rule_kind": "filter_default",
        "evidence": {"frequency": 0.92, "sample_mappers": ["OrderMapper.selectAll"]},
    }]

    total = await extract_and_write_knowledge(
        db_session,
        namespace_id=ns_id, repo_id=repo_id,
        business_terms=[],
        business_rules=business_rules,
        coll_to_db=coll_to_db,
    )
    await db_session.commit()

    assert total == 1

    rows = (await db_session.execute(
        select(KnowledgeEntry).where(
            KnowledgeEntry.namespace_id == ns_id,
            KnowledgeEntry.entry_type == "rule",
        )
    )).scalars().all()
    assert len(rows) == 1

    ke = rows[0]
    assert ke.status == "proposed"
    assert ke.source == "code_extract"
    assert ke.repo_id == repo_id

    payload = json.loads(ke.payload)
    assert payload["rule_kind"] == "filter_default"
    # Task 1b: CollectionRef form, not bare list[str]
    assert payload["applies_to_collections"] == [
        {"database": "test_db", "collection": "t_order"}
    ]
    assert payload["evidence"]["frequency"] == 0.92


@pytest.mark.asyncio
async def test_extract_and_write_knowledge_creates_example_ke(db_session, seeded):
    """D3: business_examples → entry_type=example KE 写入验证.

    Task 3: content=question, payload 含单步 final_query_plan; 死字段 source_mapper/extraction_source 移除.
    """
    ns_id, repo_id = seeded
    coll_to_db = {"orders": ("mysql", "test_db")}

    business_examples = [{
        "question": "按状态查订单并按创建时间倒序",
        "operation": "sql",
        "query": {"sql": "SELECT * FROM orders WHERE status = ? ORDER BY created_at DESC"},
        "tables": ["orders"],
    }]

    total = await extract_and_write_knowledge(
        db_session,
        namespace_id=ns_id, repo_id=repo_id,
        business_terms=[], business_rules=[],
        business_examples=business_examples,
        coll_to_db=coll_to_db,
    )
    await db_session.commit()

    assert total == 1

    rows = (await db_session.execute(
        select(KnowledgeEntry).where(
            KnowledgeEntry.namespace_id == ns_id,
            KnowledgeEntry.entry_type == "example",
        )
    )).scalars().all()
    assert len(rows) == 1

    ke = rows[0]
    assert ke.status == "proposed"
    assert ke.source == "code_extract"
    assert ke.repo_id == repo_id
    payload = json.loads(ke.payload)
    # Task 1b: CollectionRef form, not dotted strings
    assert payload["collections"] == [
        {"database": "test_db", "collection": "orders"}
    ]
    assert payload["question_pattern"] == "按状态查订单并按创建时间倒序"
    # Task 3: dead fields removed (source_mapper, extraction_source, sql_pattern)
    assert "sql_pattern" not in payload
    assert "source_mapper" not in payload


@pytest.mark.asyncio
async def test_extract_and_write_knowledge_skips_empty_example(db_session, seeded):
    """business_examples 缺 query → 跳过, 不产 example KE."""
    ns_id, repo_id = seeded
    total = await extract_and_write_knowledge(
        db_session,
        namespace_id=ns_id, repo_id=repo_id,
        business_terms=[], business_rules=[],
        business_examples=[{"tables": ["orders"], "question": "x"}],  # 无 query
    )
    await db_session.commit()
    assert total == 0

# ════════════════════════════════════════════════════════════════
#  _refs_from_names — CollectionRef 补全 helper (Task 1b)
# ════════════════════════════════════════════════════════════════


def test_refs_from_names_uses_coll_to_db():
    """裸集合名经 coll_to_db 反查补全 database → list[CollectionRef]."""
    from app.knowledge.extraction_writer import _refs_from_names
    coll_to_db = {"orders": ("mysql", "shop"), "users": ("mysql", "shop")}
    refs = _refs_from_names(["orders", "users", "unknown"], coll_to_db)
    # unknown 补不到跳过
    assert len(refs) == 2
    assert refs[0].database == "shop" and refs[0].collection == "orders"
    assert refs[1].database == "shop" and refs[1].collection == "users"


def test_refs_from_names_empty_when_no_coll_to_db():
    """coll_to_db 为 None 时返回空列表 (无法补全)."""
    from app.knowledge.extraction_writer import _refs_from_names
    assert _refs_from_names(["orders"], None) == []


def test_refs_from_names_empty_names():
    """names 为空或 None 时返回空列表."""
    from app.knowledge.extraction_writer import _refs_from_names
    assert _refs_from_names([], {"orders": ("mysql", "shop")}) == []
    assert _refs_from_names(None, {"orders": ("mysql", "shop")}) == []


def test_refs_from_names_skips_unresolvable():
    """coll_to_db 中找不到的名字被静默跳过, 不报错."""
    from app.knowledge.extraction_writer import _refs_from_names
    coll_to_db = {"orders": ("mysql", "shop")}
    assert _refs_from_names(["ghost_table"], coll_to_db) == []


@pytest.mark.asyncio
async def test_write_rule_ke_no_coll_to_db_emits_empty_list(db_session, seeded):
    """无 coll_to_db 时 applies_to_collections 为空列表 (不 422, 不 emit 裸名)."""
    ns_id, repo_id = seeded

    business_rules = [{
        "rule_text": "某规则",
        "applies_to_collections": ["t_order"],
    }]

    total = await extract_and_write_knowledge(
        db_session,
        namespace_id=ns_id, repo_id=repo_id,
        business_terms=[],
        business_rules=business_rules,
        # coll_to_db not passed (defaults to None)
    )
    await db_session.commit()
    assert total == 1

    rows = (await db_session.execute(
        select(KnowledgeEntry).where(
            KnowledgeEntry.namespace_id == ns_id,
            KnowledgeEntry.entry_type == "rule",
        )
    )).scalars().all()
    payload = json.loads(rows[0].payload)
    # No coll_to_db → cannot resolve → empty list (not bare strings → 422)
    assert payload["applies_to_collections"] == []


@pytest.mark.asyncio
async def test_write_business_examples_emits_collection_ref(db_session, seeded):
    """_write_business_examples 使用 coll_to_db 转 CollectionRef, 移除旧 compat 字段."""
    ns_id, repo_id = seeded
    coll_to_db = {"orders": ("mysql", "test_db")}

    business_examples = [{
        "question": "按状态查订单",
        "operation": "sql",
        "query": {"sql": "SELECT * FROM orders WHERE status = ?"},
        "tables": ["orders"],
    }]

    total = await extract_and_write_knowledge(
        db_session,
        namespace_id=ns_id, repo_id=repo_id,
        business_terms=[], business_rules=[],
        business_examples=business_examples,
        coll_to_db=coll_to_db,
    )
    await db_session.commit()
    assert total == 1

    rows = (await db_session.execute(
        select(KnowledgeEntry).where(
            KnowledgeEntry.namespace_id == ns_id,
            KnowledgeEntry.entry_type == "example",
        )
    )).scalars().all()
    payload = json.loads(rows[0].payload)
    # Collections are CollectionRef form
    assert payload["collections"] == [
        {"database": "test_db", "collection": "orders"}
    ]
    # Task 3: dead fields removed
    assert "sql_pattern" not in payload
