"""
启动期幂等 schema 微调 — PostgreSQL 不用 Alembic 时的兜底
策略: information_schema.columns → 差集 ADD COLUMN（单事务，消除 TOCTOU）
"""

import logging
from typing import TypeAlias

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

log = logging.getLogger(__name__)

# (column_name, SQL DDL fragment used after "ADD COLUMN <name>")
ColumnSpec: TypeAlias = tuple[str, str]

# ── 每张需兜底的表对应的「新增列 DDL」 ──────────────────────────────────────────
_KNOWLEDGE_ENTRY_NEW_COLS: list[ColumnSpec] = [
    ("tier",          "VARCHAR(16) NOT NULL DEFAULT 'normal'"),
    ("raw_input",     "TEXT NOT NULL DEFAULT ''"),
    ("description",   "TEXT NOT NULL DEFAULT ''"),
    ("is_superseded", "BOOLEAN NOT NULL DEFAULT FALSE"),
    ("refined_at",    "TIMESTAMP NULL"),
    # LLM 自动生成的知识默认 False, 需人工审阅后置 True
    # 手工录入的 source=manual 入库时直接置 True
    ("reviewed",      "BOOLEAN NOT NULL DEFAULT FALSE"),
]

# SchemaCanonicalObject(db_type='mongodb') 语义层字段
# (2026-04-22 Schema 管理; 已统一替代旧 MongoCanonicalCollection)
_MONGO_CANONICAL_NEW_COLS: list[ColumnSpec] = [
    ("description",    "TEXT NOT NULL DEFAULT ''"),
    ("purpose_detail", "TEXT NOT NULL DEFAULT ''"),
    ("reviewed",       "BOOLEAN NOT NULL DEFAULT FALSE"),
    # 代码侧声称存在但真实 MongoDB 不存在的 collection (由 merge 末尾真实性校验标记)
    ("is_orphaned",    "BOOLEAN NOT NULL DEFAULT FALSE"),
]

# GitRepo 新增词典刷新追踪列 (2026-04-28 Decomposer Routing P0)
_GIT_REPO_NEW_COLS: list[ColumnSpec] = [
    # pending | ok | partial | failed | unknown_error
    ("term_refresh_status",     "VARCHAR(20) NOT NULL DEFAULT 'pending'"),
    ("term_refresh_stats_json", "TEXT NOT NULL DEFAULT '{}'"),
]

# SchemaCanonicalObject Phase 1 新增列 (2026-05-15 schema-knowledge-onboarding)
_SCHEMA_CANONICAL_OBJECT_NEW_COLS: list[ColumnSpec] = [
    ("relationships_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("sample_values_json", "TEXT NOT NULL DEFAULT '[]'"),
    ("user_locked",        "BOOLEAN NOT NULL DEFAULT FALSE"),
]

# Stage 1 升级 (migration_007): KnowledgeEntry 引入 payload / status / evidence_json /
# superseded_by / reviewed_by_id / reviewed_at, 并把旧 reviewed bool 回灌到 status.
# 旧 reviewed 列保留但业务代码不再读它.
_KNOWLEDGE_ENTRY_STAGE1_NEW_COLS: list[ColumnSpec] = [
    ("payload",         "TEXT NOT NULL DEFAULT '{}'"),
    ("status",          "VARCHAR(16) NOT NULL DEFAULT 'proposed'"),
    ("evidence_json",   "TEXT NOT NULL DEFAULT '{}'"),
    ("superseded_by",   "INTEGER NULL REFERENCES knowledge_entries(id) ON DELETE SET NULL"),
    ("reviewed_by_id",  "INTEGER NULL REFERENCES users(id) ON DELETE SET NULL"),
    ("reviewed_at",     "TIMESTAMP NULL"),
]


# ── 内部工具函数 ────────────────────────────────────────────────────────────────


async def _column_exists(conn, table: str, column: str) -> bool:
    """检查 PostgreSQL 表中是否存在指定列"""
    result = await conn.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = :table AND column_name = :col"
        ),
        {"table": table, "col": column},
    )
    return result.scalar_one_or_none() is not None


async def _table_exists(conn, table: str) -> bool:
    """检查 PostgreSQL public schema 中是否存在指定表"""
    result = await conn.execute(
        text(
            "SELECT 1 FROM pg_tables "
            "WHERE schemaname = 'public' AND tablename = :table"
        ),
        {"table": table},
    )
    return result.scalar_one_or_none() is not None


async def _add_missing(engine: AsyncEngine, table: str, specs: list[ColumnSpec]) -> None:
    """Idempotent ADD COLUMN — PostgreSQL version using information_schema."""
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :table"
            ),
            {"table": table},
        )
        existing = {row[0] for row in result.all()}
        for col, ddl in specs:
            if col not in existing:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
                log.info("[schema_migrations] %s.%s added", table, col)


async def _ensure_pending_clarifications_table(engine: AsyncEngine) -> None:
    """幂等建 pending_clarifications 表 + 索引. Decomposer Routing P1."""
    ddl_table = """
    CREATE TABLE IF NOT EXISTS pending_clarifications (
        id SERIAL PRIMARY KEY,
        session_id VARCHAR(64) NOT NULL,
        namespace_id INTEGER NOT NULL REFERENCES namespaces(id) ON DELETE CASCADE,
        original_question TEXT NOT NULL,
        targets_json TEXT NOT NULL DEFAULT '[]',
        conditions_json TEXT NOT NULL DEFAULT '[]',
        resolved_json TEXT NOT NULL DEFAULT '{}',
        pending_cond_ids_json TEXT NOT NULL DEFAULT '[]',
        clarification_questions_json TEXT NOT NULL DEFAULT '[]',
        status VARCHAR(16) NOT NULL DEFAULT 'pending',
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
        expires_at TIMESTAMP NOT NULL
    )
    """
    ddl_idx1 = (
        "CREATE INDEX IF NOT EXISTS idx_pending_session "
        "ON pending_clarifications(session_id, status)"
    )
    ddl_idx2 = (
        "CREATE INDEX IF NOT EXISTS idx_pending_expires "
        "ON pending_clarifications(expires_at)"
    )
    async with engine.begin() as conn:
        await conn.execute(text(ddl_table))
        await conn.execute(text(ddl_idx1))
        await conn.execute(text(ddl_idx2))
    log.info("[schema_migrations] pending_clarifications table ensured")


# ── 公开接口 ────────────────────────────────────────────────────────────────────

async def ensure_knowledge_entry_columns(engine: AsyncEngine) -> None:
    """幂等地为 knowledge_entries 补全新增列"""
    await _add_missing(engine, "knowledge_entries", _KNOWLEDGE_ENTRY_NEW_COLS)


async def ensure_mongo_canonical_columns(engine: AsyncEngine) -> None:
    """幂等地为 mongo_canonical_collections 补全语义层列.

    NOTE: migration_013 会 DROP 此表. 本函数保留仅为兼容存量库升级路径
    (先补列 → 后续 migration_013 drop). 表不存在时静默跳过.
    """
    async with engine.begin() as conn:
        if not await _table_exists(conn, "mongo_canonical_collections"):
            return
    await _add_missing(engine, "mongo_canonical_collections", _MONGO_CANONICAL_NEW_COLS)


async def ensure_git_repo_columns(engine: AsyncEngine) -> None:
    """幂等地为 git_repos 补全 Decomposer Routing 追踪列"""
    await _add_missing(engine, "git_repos", _GIT_REPO_NEW_COLS)


async def ensure_knowledge_entry_stage1_columns(engine: AsyncEngine) -> None:
    """Stage 1 升级 (migration_007): 加新列 + backfill reviewed → status.

    旧字段 reviewed (bool) 保留, 但业务代码不再读它.
    backfill 规则: reviewed=true ∧ status='proposed' → status='canonical'.
    """
    await _add_missing(engine, "knowledge_entries", _KNOWLEDGE_ENTRY_STAGE1_NEW_COLS)
    async with engine.begin() as conn:
        if await _column_exists(conn, "knowledge_entries", "reviewed"):
            await conn.execute(text(
                "UPDATE knowledge_entries SET status = 'canonical' "
                "WHERE reviewed = true AND status = 'proposed'"
            ))
            log.info("[schema_migrations] knowledge_entries backfilled status from reviewed")


async def _drop_business_terms_table(engine: AsyncEngine) -> None:
    """Stage 1 Task 13: 删除已废弃的 business_terms 表.

    Task 8 数据迁移已把 BT 行迁到 KnowledgeEntry[entry_type=terminology].
    DROP TABLE IF EXISTS — 旧库无表也安全.
    """
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS business_terms"))
    log.info("[schema_migrations] business_terms table dropped (Stage 1 Task 13)")


async def _drop_namespace_rules_table(engine: AsyncEngine) -> None:
    """Stage 1 Task 14: 删除已废弃的 namespace_rules 表.

    Task 8 数据迁移已把 NR 行迁到 KnowledgeEntry[entry_type=rule, status=canonical].
    DROP TABLE IF EXISTS — 旧库无表也安全.
    """
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS namespace_rules"))
    log.info("[schema_migrations] namespace_rules table dropped (Stage 1 Task 14)")


async def _drop_mongo_collection_indexes_table(engine: AsyncEngine) -> None:
    """Stage1 driver 抽象后: 删除孤儿表 mongo_collection_indexes.

    历史: 曾用于存运行时 MongoDB introspect 出来的索引快照,
    服务 mongo_projection_builder 投影. Stage1 删除 MongoQueryEngine
    后 0 读 0 写, 永久退役 (commit 8184901).
    """
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS mongo_collection_indexes"))
    log.info("[schema_migrations] mongo_collection_indexes table dropped (post-Stage1)")


# ── migration_008 (Phase 0): 知识审核闭环 + terminology 唯一键基础设施 ───────────

async def _drop_knowledge_audit_log_table_legacy(engine: AsyncEngine) -> None:
    """migration_008 part A (REVERSED 2026-05-11): 清理历史孤儿表 knowledge_audit_log (单数).

    背景: 早期迁移建了单数名表, 同时 SQLAlchemy model 用了复数 (`knowledge_audit_logs`),
    两表并存数年, 单数表 0 行无人写. 现 DROP 单数表 + 关联索引, 代码侧统一走复数表.
    """
    async with engine.begin() as conn:
        await conn.execute(text("DROP INDEX IF EXISTS ix_audit_entry_created"))
        await conn.execute(text("DROP INDEX IF EXISTS ix_audit_ns_created"))
        await conn.execute(text("DROP INDEX IF EXISTS ix_audit_action_created"))
        await conn.execute(text("DROP TABLE IF EXISTS knowledge_audit_log"))
    log.info("[schema_migrations] legacy knowledge_audit_log table dropped (singular orphan)")


async def _create_terminology_conflicts_table(engine: AsyncEngine) -> None:
    """migration_008 part B: 建 terminology_conflicts 表 + 索引 (幂等)."""
    ddl_table = """
    CREATE TABLE IF NOT EXISTS terminology_conflicts (
        id SERIAL PRIMARY KEY,
        namespace_id INTEGER NOT NULL REFERENCES namespaces(id) ON DELETE CASCADE,
        existing_entry_id INTEGER NOT NULL REFERENCES knowledge_entries(id) ON DELETE CASCADE,
        candidate_payload TEXT NOT NULL,
        candidate_source VARCHAR(20) NOT NULL,
        candidate_repo_id INTEGER NULL REFERENCES git_repos(id) ON DELETE SET NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'open',
        resolution_choice VARCHAR(20) NULL,
        resolved_at TIMESTAMP NULL,
        resolved_by_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """
    async with engine.begin() as conn:
        await conn.execute(text(ddl_table))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_term_conflict_ns_status_created "
            "ON terminology_conflicts(namespace_id, status, created_at)"
        ))
    log.info("[schema_migrations] terminology_conflicts table ensured (migration_008)")


async def _create_terminology_partial_unique_index(engine: AsyncEngine) -> None:
    """migration_008 part C: terminology 唯一键 partial unique index (幂等).

    锚定 (namespace_id, primary_collection, primary_database, db_type),
    仅约束 entry_type='terminology' AND is_superseded = false (历史 superseded 不参与).
    """
    ddl = """
    CREATE UNIQUE INDEX IF NOT EXISTS uq_terminology_anchor
    ON knowledge_entries (
        namespace_id,
        (payload::jsonb->>'primary_collection'),
        (payload::jsonb->>'primary_database'),
        (payload::jsonb->>'db_type')
    )
    WHERE entry_type='terminology' AND is_superseded = false
    """
    async with engine.begin() as conn:
        await conn.execute(text(ddl))
    log.info("[schema_migrations] terminology partial unique index ensured (migration_008)")


async def _ensure_enum_dictionaries_table(engine: AsyncEngine) -> None:
    """migration_011: 建 enum_dictionaries 表 + 唯一索引 (幂等).

    设计: docs/superpowers/specs/2026-05-18-enum-knowledge-binding/03-enum-dictionary.md
    """
    ddl_table = """
    CREATE TABLE IF NOT EXISTS enum_dictionaries (
        id SERIAL PRIMARY KEY,
        namespace_id INTEGER NOT NULL REFERENCES namespaces(id) ON DELETE CASCADE,
        enum_class_name VARCHAR(100) NOT NULL,
        fully_qualified_name VARCHAR(200),
        values_json TEXT NOT NULL,
        scope VARCHAR(20) NOT NULL DEFAULT 'namespace',
        source VARCHAR(20) NOT NULL DEFAULT 'code',
        comment TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
        created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
        updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL
    )
    """
    async with engine.begin() as conn:
        await conn.execute(text(ddl_table))
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_enum_dict_ns_name "
            "ON enum_dictionaries(namespace_id, enum_class_name)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_enum_dict_source "
            "ON enum_dictionaries(namespace_id, source)"
        ))
    log.info("[schema_migrations] enum_dictionaries table ensured (migration_011)")


async def _ensure_enum_sync_tables(engine: AsyncEngine) -> None:
    """migration_012: enum_sync_queue + enum_binding_conflicts 表 (幂等).

    设计: docs/superpowers/specs/2026-05-18-enum-knowledge-binding/04-field-enum-binding.md §4-§5
    """
    ddl_queue = """
    CREATE TABLE IF NOT EXISTS enum_sync_queue (
        id SERIAL PRIMARY KEY,
        enum_dict_id INTEGER NOT NULL,
        namespace_id INTEGER NOT NULL,
        event VARCHAR(20) NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """
    ddl_conflict = """
    CREATE TABLE IF NOT EXISTS enum_binding_conflicts (
        id SERIAL PRIMARY KEY,
        namespace_id INTEGER NOT NULL,
        field_canonical_id INTEGER NOT NULL,
        field_name VARCHAR(100) NOT NULL,
        enum_dict_id INTEGER NOT NULL,
        conflict_kind VARCHAR(30) NOT NULL,
        detail_json TEXT NOT NULL DEFAULT '{}',
        status VARCHAR(20) NOT NULL DEFAULT 'open',
        created_at TIMESTAMP DEFAULT NOW(),
        resolved_at TIMESTAMP,
        resolved_by INTEGER
    )
    """
    async with engine.begin() as conn:
        await conn.execute(text(ddl_queue))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_enum_sync_queue_dict_id "
            "ON enum_sync_queue(enum_dict_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_enum_sync_queue_ns_id "
            "ON enum_sync_queue(namespace_id)"
        ))
        await conn.execute(text(ddl_conflict))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_enum_binding_conflicts_ns_id "
            "ON enum_binding_conflicts(namespace_id)"
        ))
        await conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_enum_conflict_open "
            "ON enum_binding_conflicts(field_canonical_id, field_name, enum_dict_id) "
            "WHERE status = 'open'"
        ))
    log.info("[schema_migrations] enum_sync_queue + enum_binding_conflicts ensured (migration_012)")


# ── ExtractorProfile (agentic-repo-extractor Phase 0) ────────────────────────

_EXTRACTOR_PROFILES_NEW_COLS: list[ColumnSpec] = [
    ("profile_id", "INT REFERENCES extractor_profiles(id) ON DELETE SET NULL"),
]


async def _ensure_extractor_profiles_table(engine: AsyncEngine) -> None:
    """幂等建 extractor_profiles 表 + GIN 索引. Agentic extractor Phase 0."""
    ddl_table = """
    CREATE TABLE IF NOT EXISTS extractor_profiles (
        id            SERIAL PRIMARY KEY,
        name          VARCHAR(100) NOT NULL,
        display_name  VARCHAR(200) NOT NULL,
        description   TEXT NOT NULL DEFAULT '',
        languages     JSONB NOT NULL DEFAULT '["Java"]',
        hint_text     TEXT NOT NULL DEFAULT '',
        is_builtin    BOOLEAN NOT NULL DEFAULT FALSE,
        is_enabled    BOOLEAN NOT NULL DEFAULT TRUE,
        namespace_id  INT REFERENCES namespaces(id) ON DELETE CASCADE,
        created_at    TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'Asia/Shanghai'),
        updated_at    TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'Asia/Shanghai'),
        UNIQUE (name, namespace_id)
    )
    """
    ddl_idx = (
        "CREATE INDEX IF NOT EXISTS idx_profile_langs "
        "ON extractor_profiles USING GIN (languages)"
    )
    async with engine.begin() as conn:
        await conn.execute(text(ddl_table))
        await conn.execute(text(ddl_idx))
    log.info("[schema_migrations] extractor_profiles table ensured")


async def _ensure_model_configs_table(engine: AsyncEngine) -> None:
    """migration_023 (model-management): model_configs 表.

    存储多厂商 LLM / Embedding 配置，支持运行时热切换。
    api_key 由 EncryptedString TypeDecorator 在应用层 Fernet 加密后入库。
    """
    ddl = """
    CREATE TABLE IF NOT EXISTS model_configs (
        id                SERIAL PRIMARY KEY,
        provider          VARCHAR(64)   NOT NULL,
        base_url          VARCHAR(512)  NOT NULL,
        api_key           TEXT          NOT NULL,
        model_name        VARCHAR(128)  NOT NULL,
        model_type        VARCHAR(20)   NOT NULL DEFAULT 'CHAT',
        temperature       NUMERIC(4,2)  NULL DEFAULT 0.00,
        max_tokens        INTEGER       NULL DEFAULT 12288,
        completions_path  VARCHAR(256)  NULL,
        embeddings_path   VARCHAR(256)  NULL,
        proxy_enabled     BOOLEAN       NOT NULL DEFAULT FALSE,
        proxy_host        VARCHAR(256)  NULL,
        proxy_port        INTEGER       NULL,
        proxy_username    VARCHAR(128)  NULL,
        proxy_password    TEXT          NULL,
        is_active         BOOLEAN       NOT NULL DEFAULT FALSE,
        is_deleted        BOOLEAN       NOT NULL DEFAULT FALSE,
        created_at        TIMESTAMP     NOT NULL DEFAULT (now() AT TIME ZONE 'Asia/Shanghai'),
        updated_at        TIMESTAMP     NULL
    )
    """
    idx_type = (
        "CREATE INDEX IF NOT EXISTS idx_model_configs_type_active "
        "ON model_configs (model_type, is_active) WHERE is_deleted = FALSE"
    )
    dedupe_active = """
    WITH ranked AS (
        SELECT
            id,
            ROW_NUMBER() OVER (
                PARTITION BY model_type
                ORDER BY updated_at DESC NULLS LAST, id DESC
            ) AS rn
        FROM model_configs
        WHERE is_active = TRUE AND is_deleted = FALSE
    )
    UPDATE model_configs AS mc
    SET
        is_active = FALSE,
        updated_at = (now() AT TIME ZONE 'Asia/Shanghai')
    FROM ranked
    WHERE mc.id = ranked.id AND ranked.rn > 1
    """
    unique_active = (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_model_configs_one_active_per_type "
        "ON model_configs (model_type) "
        "WHERE is_active = TRUE AND is_deleted = FALSE"
    )
    alter_max_tokens_default = (
        "ALTER TABLE model_configs "
        "ALTER COLUMN max_tokens SET DEFAULT 12288"
    )
    async with engine.begin() as conn:
        await conn.execute(text(ddl))
        await conn.execute(text(dedupe_active))
        await conn.execute(text(idx_type))
        await conn.execute(text(unique_active))
        await conn.execute(text(alter_max_tokens_default))
    log.info("[schema_migrations] model_configs table ensured (migration_023)")


async def _repair_terminology_conflict_cascade_fk(engine: AsyncEngine) -> None:
    """确保 terminology_conflicts.existing_entry_id 的 FK 为 ON DELETE CASCADE.

    模型声明 ondelete='CASCADE', 但长生命周期库的旧表在 FK 添加前已建, create_all
    不改既有表约束 → FK 缺失/非 cascade。purge 删 KE 时无法级联清 resolved conflict
    (残留孤儿)。本迁移按需重建 FK (幂等: 已是 cascade 则跳过)。
    """
    ddl = """
    DO $$
    DECLARE con_name text; del_type char;
    BEGIN
        SELECT con.conname, con.confdeltype INTO con_name, del_type
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        JOIN pg_attribute a ON a.attnum = ANY(con.conkey) AND a.attrelid = con.conrelid
        WHERE rel.relname = 'terminology_conflicts' AND con.contype = 'f'
          AND a.attname = 'existing_entry_id'
        LIMIT 1;
        IF con_name IS NULL OR del_type <> 'c' THEN
            -- 先清孤儿行 (existing_entry_id 指向已删 KE), 否则 ADD CONSTRAINT 失败
            DELETE FROM terminology_conflicts tc
            WHERE NOT EXISTS (
                SELECT 1 FROM knowledge_entries ke WHERE ke.id = tc.existing_entry_id
            );
            IF con_name IS NOT NULL THEN
                EXECUTE 'ALTER TABLE terminology_conflicts DROP CONSTRAINT '
                    || quote_ident(con_name);
            END IF;
            ALTER TABLE terminology_conflicts
                ADD CONSTRAINT terminology_conflicts_existing_entry_id_fkey
                FOREIGN KEY (existing_entry_id) REFERENCES knowledge_entries(id) ON DELETE CASCADE;
        END IF;
    END $$;
    """
    async with engine.begin() as conn:
        await conn.execute(text(ddl))
    log.info(
        "[schema_migrations] terminology_conflicts existing_entry_id FK ensured "
        "ON DELETE CASCADE"
    )


async def run_all(engine: AsyncEngine) -> None:
    """Run all startup schema migrations idempotently. Extend here for new tables."""
    await ensure_knowledge_entry_columns(engine)
    await ensure_mongo_canonical_columns(engine)
    await ensure_git_repo_columns(engine)
    await _ensure_pending_clarifications_table(engine)
    await ensure_knowledge_entry_stage1_columns(engine)
    await _drop_business_terms_table(engine)
    await _drop_namespace_rules_table(engine)
    # migration_008 (Phase 0): terminology_conflicts + partial unique index
    # 注: knowledge_audit_log (单数) 历史孤儿表 2026-05-11 起改为 DROP, 真实表是
    # SQLAlchemy 自动建的 knowledge_audit_logs (复数, KnowledgeAuditLog model)
    await _drop_knowledge_audit_log_table_legacy(engine)
    await _create_terminology_conflicts_table(engine)
    await _create_terminology_partial_unique_index(engine)
    # migration_009 (Stage 2): schema_canonical_objects 通用 schema 真相源
    await _ensure_schema_canonical_objects_table(engine)
    # schema-knowledge-onboarding Phase 1: schema_canonical_objects 新增 3 列
    await _add_missing(engine, "schema_canonical_objects", _SCHEMA_CANONICAL_OBJECT_NEW_COLS)
    # migration_010 (schema-knowledge-onboarding Phase 1): candidate / conflict /
    # canonical_audit_log / extraction_failure_log 四张新表 + partial unique index.
    # 修订 #4 要求 conflict 表 partial unique 仅约束 status='open' 行.
    await _ensure_schema_canonical_phase1_tables(engine)
    # post-Stage1: drop 孤儿表 mongo_collection_indexes (运行时索引特性退役)
    await _drop_mongo_collection_indexes_table(engine)
    # migration_011 (enum-knowledge-binding): enum_dictionaries 表
    await _ensure_enum_dictionaries_table(engine)
    # migration_012 (enum-knowledge-binding Phase 2): enum_sync_queue + enum_binding_conflicts
    await _ensure_enum_sync_tables(engine)
    # migration_013 (mongo-canonical-retirement): drop 中间层三张表
    await _drop_mongo_canonical_layer(engine)
    # migration_014 (Stage 2 抓手 A): hypothetical_queries_json 列
    await _add_missing(engine, "knowledge_entries", [
        ("hypothetical_queries_json", "TEXT NOT NULL DEFAULT '[]'"),
    ])
    # migration_015 (Stage 2 抓手 B): 召回反馈环 4 列
    await _add_missing(engine, "knowledge_entries", [
        ("recall_count", "INTEGER NOT NULL DEFAULT 0"),
        ("adopted_count", "INTEGER NOT NULL DEFAULT 0"),
        ("negative_signal_count", "INTEGER NOT NULL DEFAULT 0"),
        ("last_recalled_at", "TIMESTAMP NULL"),
    ])
    # migration_016 (Stage 2 抓手 D): A-MEM related_entry_ids
    await _add_missing(engine, "knowledge_entries", [
        ("related_entry_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
    ])
    # migration_017 (Stage 2 抓手 E): agent_traces 新表
    await _ensure_agent_traces_table(engine)
    # migration_018 (partial-index-repair): 修复 conflict 唯一索引被建成全表
    # unique 的历史问题 (model 误用 sqlite_where, PostgreSQL 上被忽略 →
    # create_all 建成全表 unique, resolved 行占位阻挡同字段重新开 conflict).
    await _repair_open_conflict_partial_indexes(engine)
    # migration_019 (schema-snapshot-retirement): drop datasources.schema_snapshot_json
    # 列. /collections 接口 mysql+mongodb 已统一走 SchemaCanonicalObject 真相源,
    # snapshot 列 0 读 0 写, 永久退役.
    await _drop_datasource_schema_snapshot_column(engine)
    # migration_021 (three-tier-rbac): role 扩列 + ns.created_by + admin 升级 + 回填 + FK 修复
    await _migrate_rbac_three_tier(engine)
    # migration_022 (datasource-catalog): DataSource 加 description + db_profile_json.
    # 库级画像字段 — description 用户手填用途, db_profile_json 建源连库合成 (版本/charset/对象数).
    # 跟随 fields_json 惯例用 TEXT 存 JSON 串, 不用 JSONB.
    await _add_missing(engine, "datasources", [
        ("description", "TEXT NOT NULL DEFAULT ''"),
        ("db_profile_json", "TEXT NOT NULL DEFAULT '{}'"),
    ])
    # agentic-repo-extractor Phase 0: extractor_profiles 表 + git_repos.profile_id FK + 种子数据
    await _ensure_extractor_profiles_table(engine)
    await _add_missing(engine, "git_repos", _EXTRACTOR_PROFILES_NEW_COLS)
    from app.db.seed_data import ensure_extractor_profile_seeds
    await ensure_extractor_profile_seeds(engine)
    # 修复 terminology_conflicts.existing_entry_id FK 缺失 ON DELETE CASCADE (长生命周期库漂移)
    await _repair_terminology_conflict_cascade_fk(engine)
    # migration_023 (model-management): model_configs 表 — 多厂商模型配置持久化 + 热切换
    await _ensure_model_configs_table(engine)
    # migration_024 (model-config-protocol): model_configs.protocol 列 — openai | anthropic
    await _ensure_model_config_protocol_column(engine)
    # migration_025 (model-management): model_config_audit_logs 审计日志表
    await _ensure_model_config_audit_logs_table(engine)
    # migration_026 (P0 对话体验优化): sessions 表 — 对话会话持久化
    await _create_sessions_table(engine)
    # migration_027 (P0 对话体验优化): query_history.feedback_rating 列 — 答案反馈
    await _add_missing(engine, "query_history", [
        ("feedback_rating", "VARCHAR(10)"),
    ])
    # migration_028 (timezone): datasources.timezone 列 + 存量回填
    await _ensure_datasources_timezone_column(engine)


async def _create_sessions_table(engine: AsyncEngine) -> None:
    """migration_026: sessions 表 — 会话 CRUD, 绑定命名空间, 软删不实施."""
    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE IF NOT EXISTS sessions ("
            "    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),"
            "    namespace_id INTEGER NOT NULL REFERENCES namespaces(id) ON DELETE CASCADE,"
            "    title VARCHAR(255) NOT NULL DEFAULT '新会话',"
            "    created_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,"
            "    created_at TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'Asia/Shanghai'),"
            "    updated_at TIMESTAMP DEFAULT (now() AT TIME ZONE 'Asia/Shanghai')"
            ")"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_sessions_namespace_updated"
            "    ON sessions(namespace_id, updated_at DESC)"
        ))


async def _migrate_rbac_three_tier(engine: AsyncEngine) -> None:
    """migration_021 (three-tier-rbac): role 扩列 + namespace.created_by
    + bootstrap admin 升 super_admin + 存量 admin 访问权回填 + 自引用 FK 修复。
    单事务, 幂等。"""
    async with engine.begin() as conn:
        # ★ 一次性判据: 回填只在"从旧 schema 首次迁移"时执行。role 列宽是天然的
        #   迁移版本标记 — 旧库 VARCHAR(10), 迁移后 VARCHAR(20)。若不以此设防,
        #   run_all 每次启动都重跑回填, 把"每个 admin × 每个 namespace"的 access
        #   行补全 (NOT EXISTS 只防重复行, 不防语义错误) → admin 作用域被永久架空,
        #   迁移后新建的 admin 一旦经历重启就重新获得全部 namespace 访问。
        width = await conn.scalar(text(
            "SELECT character_maximum_length FROM information_schema.columns "
            "WHERE table_name='users' AND column_name='role'"
        ))
        is_first_migration = width is not None and width < 20

        # 1. role 列扩宽 (VARCHAR(10)→VARCHAR(20)) — 容纳 'super_admin'
        await conn.execute(text(
            "ALTER TABLE users ALTER COLUMN role TYPE VARCHAR(20)"
        ))
        # 2. namespaces.created_by (幂等)
        await conn.execute(text(
            "ALTER TABLE namespaces ADD COLUMN IF NOT EXISTS created_by INTEGER "
            "REFERENCES users(id) ON DELETE SET NULL"
        ))
        # 3. bootstrap 账号 admin → super_admin (幂等: 二次命中 0 行)
        await conn.execute(text(
            "UPDATE users SET role='super_admin' "
            "WHERE username='admin' AND role='admin'"
        ))
        # 4. ★ 语义保持回填 (仅首次迁移): 残留 admin × 全部现存 ns 的 UserNamespaceAccess。
        #    迁移前 admin 全局可见, 升级后改 owner∪granted 作用域 — 首次迁移必须回填,
        #    否则瞬间失去全部数据访问。但只能跑一次: 迁移后新建的 admin 应按作用域语义
        #    管理, 不能被后续启动的 run_all 重新提权为"全局可见"。
        if is_first_migration:
            await conn.execute(text(
                "INSERT INTO user_namespace_access (user_id, namespace_id) "
                "SELECT u.id, n.id FROM users u CROSS JOIN namespaces n "
                "WHERE u.role='admin' AND NOT EXISTS ("
                "  SELECT 1 FROM user_namespace_access una "
                "  WHERE una.user_id=u.id AND una.namespace_id=n.id)"
            ))
        # 5. ★ 自引用 FK 补 ON DELETE SET NULL (存量库修复)。旧 users.created_by FK
        #    无 ondelete (PG 默认 NO ACTION) → 删有下属的 admin 触发 IntegrityError 500。
        #    幂等: 查现有 delete_rule, 已是 SET NULL 跳过; 否则 drop 旧约束 + 建新。
        fk_rule = await conn.scalar(text(
            "SELECT rc.delete_rule FROM information_schema.referential_constraints rc "
            "JOIN information_schema.table_constraints tc "
            "  ON rc.constraint_name = tc.constraint_name "
            "WHERE tc.table_name='users' AND tc.constraint_type='FOREIGN KEY' "
            "  AND rc.constraint_name LIKE '%created_by%'"
        ))
        if fk_rule is not None and fk_rule != "SET NULL":
            cname = await conn.scalar(text(
                "SELECT tc.constraint_name FROM information_schema.table_constraints tc "
                "WHERE tc.table_name='users' AND tc.constraint_type='FOREIGN KEY' "
                "  AND tc.constraint_name LIKE '%created_by%'"
            ))
            await conn.execute(text(f"ALTER TABLE users DROP CONSTRAINT {cname}"))
            await conn.execute(text(
                "ALTER TABLE users ADD CONSTRAINT users_created_by_fkey "
                "FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL"
            ))
    log.info("[schema_migrations] three-tier RBAC migrated + backfilled + FK fixed (migration_021)")


async def _ensure_schema_canonical_objects_table(engine: AsyncEngine) -> None:
    """migration_009: 建 schema_canonical_objects 表 + 唯一索引 (幂等)."""
    ddl_table = """
    CREATE TABLE IF NOT EXISTS schema_canonical_objects (
        id SERIAL PRIMARY KEY,
        namespace_id INTEGER NOT NULL REFERENCES namespaces(id) ON DELETE CASCADE,
        db_type VARCHAR(20) NOT NULL,
        database VARCHAR(100) NOT NULL,
        target VARCHAR(200) NOT NULL,
        fields_json TEXT NOT NULL DEFAULT '[]',
        indexes_json TEXT NOT NULL DEFAULT '[]',
        description TEXT NOT NULL DEFAULT '',
        purpose_detail TEXT NOT NULL DEFAULT '',
        reviewed BOOLEAN NOT NULL DEFAULT FALSE,
        sample_count INTEGER NOT NULL DEFAULT 0,
        source VARCHAR(20) NOT NULL DEFAULT 'introspect',
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NULL
    )
    """
    ddl_idx = (
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_sco_ns_dbtype_db_target "
        "ON schema_canonical_objects(namespace_id, db_type, database, target)"
    )
    async with engine.begin() as conn:
        await conn.execute(text(ddl_table))
        await conn.execute(text(ddl_idx))
    log.info("[schema_migrations] schema_canonical_objects table ensured (migration_009)")


# ── migration_010 (schema-knowledge-onboarding Phase 1) ────────────────
# 四张新表 + partial unique index. 模型: app/models/schema_canonical_*.py
# + extraction_failure_log.py. 与 metadata.create_all 等价 DDL, 但显式落
# 在 migration 路径里, 保证存量库升级时也建表 + 建 partial unique index
# (create_all 对已存在表不补索引, 修订 #4 要求 partial unique).


async def _ensure_schema_canonical_phase1_tables(engine: AsyncEngine) -> None:
    """migration_010: Phase 1 candidate / conflict / canonical_audit_log /
    extraction_failure_log 表 + 索引, 全部 IF NOT EXISTS 幂等.
    """
    ddl_candidate = """
    CREATE TABLE IF NOT EXISTS schema_canonical_candidates (
        id SERIAL PRIMARY KEY,
        namespace_id INTEGER NOT NULL REFERENCES namespaces(id) ON DELETE CASCADE,
        db_type VARCHAR(16) NOT NULL,
        database VARCHAR(100) NOT NULL,
        target VARCHAR(200) NOT NULL,
        field_path VARCHAR(200) NOT NULL DEFAULT '',
        candidate_kind VARCHAR(32) NOT NULL,
        candidate_value_json TEXT NOT NULL,
        value_hash VARCHAR(64) NOT NULL,
        evidence_sources_json TEXT NOT NULL DEFAULT '[]',
        status VARCHAR(20) NOT NULL DEFAULT 'pending',
        confidence_status VARCHAR(32) NOT NULL DEFAULT 'unverified',
        repo_id INTEGER NULL REFERENCES git_repos(id) ON DELETE SET NULL,
        datasource_id INTEGER NULL REFERENCES datasources(id) ON DELETE SET NULL,
        generation INTEGER NOT NULL DEFAULT 0,
        promoted_at TIMESTAMP NULL,
        rejected_at TIMESTAMP NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """
    ddl_candidate_indexes = [
        "CREATE INDEX IF NOT EXISTS ix_schema_canonical_candidates_namespace_id "
        "ON schema_canonical_candidates(namespace_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_candidate_dedup "
        "ON schema_canonical_candidates"
        "(namespace_id, db_type, database, target, field_path, candidate_kind, value_hash)",
        "CREATE INDEX IF NOT EXISTS idx_candidate_pending "
        "ON schema_canonical_candidates(namespace_id, status)",
        "CREATE INDEX IF NOT EXISTS idx_candidate_field "
        "ON schema_canonical_candidates"
        "(namespace_id, db_type, database, target, field_path, candidate_kind)",
    ]

    ddl_conflict = """
    CREATE TABLE IF NOT EXISTS schema_canonical_conflicts (
        id SERIAL PRIMARY KEY,
        namespace_id INTEGER NOT NULL REFERENCES namespaces(id) ON DELETE CASCADE,
        db_type VARCHAR(16) NOT NULL,
        database VARCHAR(100) NOT NULL,
        target VARCHAR(200) NOT NULL,
        field_path VARCHAR(200) NOT NULL DEFAULT '',
        candidate_kind VARCHAR(32) NOT NULL,
        conflict_type VARCHAR(32) NOT NULL,
        candidate_ids_json TEXT NOT NULL,
        candidates_snapshot_json TEXT NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'open',
        resolution_choice VARCHAR(20) NULL,
        resolution_value_json TEXT NULL,
        resolved_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
        resolved_at TIMESTAMP NULL,
        resolution_reason TEXT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """
    ddl_conflict_indexes = [
        "CREATE INDEX IF NOT EXISTS ix_schema_canonical_conflicts_namespace_id "
        "ON schema_canonical_conflicts(namespace_id)",
        "CREATE INDEX IF NOT EXISTS idx_conflict_open "
        "ON schema_canonical_conflicts(namespace_id, status)",
        # 修订 #4 partial unique: 仅 status='open' 行参与, resolved 行不占位
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_one_open_conflict_per_field "
        "ON schema_canonical_conflicts"
        "(namespace_id, db_type, database, target, field_path, candidate_kind) "
        "WHERE status = 'open'",
    ]

    ddl_audit = """
    CREATE TABLE IF NOT EXISTS schema_canonical_audit_logs (
        id SERIAL PRIMARY KEY,
        namespace_id INTEGER NOT NULL REFERENCES namespaces(id) ON DELETE CASCADE,
        candidate_id INTEGER NULL REFERENCES schema_canonical_candidates(id) ON DELETE SET NULL,
        conflict_id INTEGER NULL REFERENCES schema_canonical_conflicts(id) ON DELETE SET NULL,
        canonical_id INTEGER NULL REFERENCES schema_canonical_objects(id) ON DELETE SET NULL,
        action VARCHAR(40) NOT NULL,
        field_path VARCHAR(200) NULL,
        before_json TEXT NULL,
        after_json TEXT NULL,
        reason TEXT NULL,
        actor_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
        extra_json TEXT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """
    ddl_audit_indexes = [
        "CREATE INDEX IF NOT EXISTS ix_schema_canonical_audit_logs_namespace_id "
        "ON schema_canonical_audit_logs(namespace_id)",
    ]

    ddl_failure = """
    CREATE TABLE IF NOT EXISTS extraction_failure_logs (
        id SERIAL PRIMARY KEY,
        namespace_id INTEGER NOT NULL REFERENCES namespaces(id) ON DELETE CASCADE,
        repo_id INTEGER NULL REFERENCES git_repos(id) ON DELETE SET NULL,
        datasource_id INTEGER NULL REFERENCES datasources(id) ON DELETE SET NULL,
        extraction_kind VARCHAR(32) NOT NULL,
        source_file VARCHAR(500) NULL,
        source_mapper VARCHAR(200) NULL,
        source_method VARCHAR(200) NULL,
        source_content TEXT NULL,
        failure_type VARCHAR(40) NOT NULL,
        failure_message TEXT NOT NULL,
        failure_extra_json TEXT NULL,
        retry_count INTEGER NOT NULL DEFAULT 0,
        last_seen_at TIMESTAMP NOT NULL DEFAULT NOW(),
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """
    ddl_failure_indexes = [
        "CREATE INDEX IF NOT EXISTS ix_extraction_failure_logs_namespace_id "
        "ON extraction_failure_logs(namespace_id)",
    ]

    async with engine.begin() as conn:
        await conn.execute(text(ddl_candidate))
        for stmt in ddl_candidate_indexes:
            await conn.execute(text(stmt))
        await conn.execute(text(ddl_conflict))
        for stmt in ddl_conflict_indexes:
            await conn.execute(text(stmt))
        await conn.execute(text(ddl_audit))
        for stmt in ddl_audit_indexes:
            await conn.execute(text(stmt))
        await conn.execute(text(ddl_failure))
        for stmt in ddl_failure_indexes:
            await conn.execute(text(stmt))
    log.info(
        "[schema_migrations] Phase 1 tables (candidate / conflict / audit_log / "
        "failure_log) + partial unique index ensured (migration_010)"
    )


async def _repair_open_conflict_partial_indexes(engine: AsyncEngine) -> None:
    """migration_018 (partial-index-repair): 把误建为全表 unique 的两个
    "单字段同时只能一个 open conflict" 索引重建为 PostgreSQL partial unique
    (WHERE status='open').

    历史原因: model __table_args__ 仅写 sqlite_where, PostgreSQL 上该谓词被
    忽略 → Base.metadata.create_all 先建成全表 unique 索引 → 后续 migration_010
    的 CREATE ... IF NOT EXISTS 见同名索引已存在直接跳过, 全表约束永久生效.
    后果: resolved 行占位, 同字段无法重新开 conflict, promote 撞 UniqueViolation.

    修复策略: DROP 旧索引 → 按 partial 谓词重建. 重建前已验证无重复 open 行,
    且本函数对每个索引检查现有 indexdef, 已是 partial (含 WHERE) 则跳过, 保证
    幂等 & 不误删正确索引.
    """
    targets = [
        (
            "uq_one_open_conflict_per_field",
            "schema_canonical_conflicts",
            "(namespace_id, db_type, database, target, field_path, candidate_kind)",
        ),
        (
            "uq_enum_conflict_open",
            "enum_binding_conflicts",
            "(field_canonical_id, field_name, enum_dict_id)",
        ),
    ]
    async with engine.begin() as conn:
        for index_name, table, cols in targets:
            indexdef = (await conn.execute(
                text("SELECT indexdef FROM pg_indexes WHERE indexname = :n"),
                {"n": index_name},
            )).scalar_one_or_none()
            # 已是 partial (含 WHERE 谓词) → 幂等跳过
            if indexdef is not None and "WHERE" in indexdef.upper():
                continue
            await conn.execute(text(f"DROP INDEX IF EXISTS {index_name}"))
            await conn.execute(text(
                f"CREATE UNIQUE INDEX {index_name} ON {table}{cols} "
                "WHERE status = 'open'"
            ))
            log.info(
                "[schema_migrations] rebuilt %s as partial unique (WHERE status='open') "
                "(migration_018)", index_name,
            )


async def _drop_datasource_schema_snapshot_column(engine: AsyncEngine) -> None:
    """migration_019 (schema-snapshot-retirement): drop datasources.schema_snapshot_json.

    背景: schema_snapshot_json 曾是 /collections 接口 mysql 分支的二级下拉数据源
    (训练时实时 SHOW TABLES 刷新). mongodb 分支早已改读 SchemaCanonicalObject,
    mysql 分支现也统一切到 SCO 真相源, 该列 0 读 0 写. DROP COLUMN IF EXISTS
    保证幂等重跑 (旧库无列也安全).
    """
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE datasources DROP COLUMN IF EXISTS schema_snapshot_json"
        ))
    log.info(
        "[schema_migrations] datasources.schema_snapshot_json column dropped "
        "(migration_019)"
    )


async def _drop_mongo_canonical_layer(engine: AsyncEngine) -> None:
    """migration_013 (mongo-canonical-retirement): drop 中间层三张表.

    三表无 FK 关联, 顺序无关. IF EXISTS 保证幂等重跑.
    索引随表自动删除 (PostgreSQL CASCADE 行为).
    """
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS mongo_knowledge_conflicts"))
        await conn.execute(text("DROP TABLE IF EXISTS mongo_canonical_collections"))
        await conn.execute(text("DROP TABLE IF EXISTS mongo_source_fragments"))
    log.info(
        "[schema_migrations] mongo canonical layer dropped: "
        "mongo_knowledge_conflicts + mongo_canonical_collections + mongo_source_fragments "
        "(migration_013)"
    )


async def _ensure_model_config_protocol_column(engine: AsyncEngine) -> None:
    """migration_024 (model-config-protocol): 幂等为 model_configs 加 protocol 列.

    - ADD COLUMN IF NOT EXISTS protocol VARCHAR(32) NOT NULL DEFAULT 'openai'
    - 回填: provider ILIKE 'anthropic' → protocol = 'anthropic'
    """
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE model_configs ADD COLUMN IF NOT EXISTS "
            "protocol VARCHAR(32) NOT NULL DEFAULT 'openai'"
        ))
        await conn.execute(text(
            "UPDATE model_configs SET protocol = 'anthropic' "
            "WHERE LOWER(provider) = 'anthropic'"
        ))
    log.info("[schema_migrations] model_configs.protocol column ensured (migration_024)")


async def _ensure_agent_traces_table(engine: AsyncEngine) -> None:
    """migration_017 (Stage 2 抓手 E): agent_traces 新表 + 索引 (幂等)."""
    ddl = """
    CREATE TABLE IF NOT EXISTS agent_traces (
        id SERIAL PRIMARY KEY,
        trace_id VARCHAR(64) NOT NULL UNIQUE,
        namespace_id INTEGER REFERENCES namespaces(id) ON DELETE CASCADE,
        user_query TEXT NOT NULL DEFAULT '',
        trace_json TEXT NOT NULL DEFAULT '{}',
        reflection_log_json TEXT NOT NULL DEFAULT '[]',
        status VARCHAR(16) NOT NULL DEFAULT 'completed',
        refined_at TIMESTAMP NULL,
        refined_summary TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMP NOT NULL DEFAULT NOW()
    )
    """
    idx_trace = (
        "CREATE INDEX IF NOT EXISTS ix_agent_traces_trace_id "
        "ON agent_traces (trace_id)"
    )
    idx_status = (
        "CREATE INDEX IF NOT EXISTS ix_agent_traces_status "
        "ON agent_traces (status, created_at DESC)"
    )
    # migration_018: session_id 列 — trace/session 解耦后按会话聚合 (幂等, nullable)
    alter_session = (
        "ALTER TABLE agent_traces ADD COLUMN IF NOT EXISTS session_id VARCHAR(64)"
    )
    idx_session = (
        "CREATE INDEX IF NOT EXISTS ix_agent_traces_session_id "
        "ON agent_traces (session_id)"
    )
    async with engine.begin() as conn:
        await conn.execute(text(ddl))
        await conn.execute(text(idx_trace))
        await conn.execute(text(idx_status))
        await conn.execute(text(alter_session))
        await conn.execute(text(idx_session))
    log.info("[schema_migrations] agent_traces table ensured (migration_017+018 session_id)")


async def _ensure_model_config_audit_logs_table(engine: AsyncEngine) -> None:
    """migration_025 (model-management): model_config_audit_logs 审计日志表."""
    ddl = """
    CREATE TABLE IF NOT EXISTS model_config_audit_logs (
        id           SERIAL PRIMARY KEY,
        config_id    INTEGER NULL REFERENCES model_configs(id) ON DELETE SET NULL,
        actor_id     INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
        action       VARCHAR(40) NOT NULL,
        model_type   VARCHAR(20) NULL,
        provider     VARCHAR(64) NULL,
        protocol     VARCHAR(32) NULL,
        model_name   VARCHAR(128) NULL,
        before_json  TEXT NULL,
        after_json   TEXT NULL,
        reason       TEXT NULL,
        created_at   TIMESTAMP NOT NULL DEFAULT (now() AT TIME ZONE 'Asia/Shanghai')
    )
    """
    async with engine.begin() as conn:
        await conn.execute(text(ddl))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_model_config_audit_config_id "
            "ON model_config_audit_logs(config_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_model_config_audit_actor_id "
            "ON model_config_audit_logs(actor_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS idx_model_config_audit_created_at "
            "ON model_config_audit_logs(created_at DESC)"
        ))
    log.info("[schema_migrations] model_config_audit_logs table ensured (migration_025)")


async def _ensure_datasources_timezone_column(engine: AsyncEngine) -> None:
    """migration_028 (timezone): 幂等为 datasources 加 timezone 列 (NOT NULL, 无默认 — 建源必填).

    照抄 migration_024 范式: ADD COLUMN ... NOT NULL DEFAULT 一步原子 (存量行填 Asia/Shanghai),
    随后 DROP DEFAULT 强制建源时显式给值
    (DataSourceCreate.timezone 必填, 用户确认是真相, 不靠 DB 默认兜底).
    """
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE datasources ADD COLUMN IF NOT EXISTS "
            "timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Shanghai'"
        ))
        await conn.execute(text(
            "ALTER TABLE datasources ALTER COLUMN timezone DROP DEFAULT"
        ))
    log.info("[schema_migrations] datasources.timezone column ensured (migration_026)")
