"""
命名空间 CRUD + 数据源管理

Stage 1 Task 14: NamespaceRule 已废弃, 规则统一走 KnowledgeEntry[entry_type=rule]
Stage 2 Task 4: DELETE namespace 走 BulkOpGuard + confirm_token 防误操作
"""

import hashlib
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import (
    ROLE_ADMIN,
    accessible_namespace_ids,
    assert_ns_access,
    assert_ns_owner,
    get_current_user,
    require_admin_or_above,
    require_ns_manage,
)
from app.config import settings
from app.db.metadata import get_db
from app.engine.drivers import get_driver
from app.engine.registry import delete_knowledge_collection
from app.knowledge.bulk_guard import BulkOperationGuard
from app.models import DataSource, Namespace
from app.models.model_config import ModelConfig
from app.models.schema_canonical_object import SchemaCanonicalObject
from app.models.user import User, UserNamespaceAccess
from app.schemas import (
    BlockerOut,
    DataSourceCreate,
    DataSourceOut,
    DataSourceProbeIn,
    DataSourceProbeOut,
    NamespaceCreate,
    NamespaceDeletePreview,
    NamespaceOut,
    NamespaceUpdate,
    ReadinessOut,
    SchemaRefreshResult,
)

router = APIRouter(prefix="/api/namespaces", tags=["namespaces"])

log = logging.getLogger(__name__)


# ════════════════════════════════════════════
#  命名空间 CRUD
# ════════════════════════════════════════════

@router.get("", response_model=list[NamespaceOut])
async def list_namespaces(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出命名空间 — super_admin 见全部; admin/user 见 owner∪granted。"""
    allowed = await accessible_namespace_ids(db, user)
    stmt = select(Namespace).order_by(Namespace.created_at.desc())
    if allowed is not None:
        stmt = stmt.where(Namespace.id.in_(allowed))
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=NamespaceOut, status_code=201)
async def create_namespace(
    body: NamespaceCreate,
    actor: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    """创建命名空间。记录 created_by; admin 自动获得访问权 (super_admin 全局无需)。"""
    ns = Namespace(
        name=body.name, slug=body.slug, description=body.description,
        created_by=actor.id,
    )
    db.add(ns)
    await db.flush()
    if actor.role == ROLE_ADMIN:
        db.add(UserNamespaceAccess(user_id=actor.id, namespace_id=ns.id))
    await db.commit()
    await db.refresh(ns)
    return ns


@router.put("/{ns_id}", response_model=NamespaceOut)
async def update_namespace(
    ns_id: int,
    body: NamespaceUpdate,
    actor: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    ns = await _get_namespace(db, ns_id)
    await assert_ns_owner(db, actor, ns_id)
    if body.name is not None:
        ns.name = body.name
    if body.description is not None:
        ns.description = body.description
    await db.commit()
    await db.refresh(ns)
    return ns


@router.delete("/{ns_id}")
async def delete_namespace(
    ns_id: int,
    dry_run: bool = Query(
        True,
        description="dry_run=true 仅返回 NamespaceDeletePreview, 不动数据",
    ),
    confirm_token: str | None = Query(
        None,
        description="dry_run=false 且 affected_count > 阈值时必填; 由 dry_run 报告下发",
    ),
    actor: User = Depends(require_admin_or_above),
    db: AsyncSession = Depends(get_db),
):
    """命名空间删除 (Stage 2 Task 4) — BulkOpGuard 接管 KE 删除 + 大批量确认机制.

    三态语义:
        dry_run=True
            → 200 NamespaceDeletePreview (含 affected_count / by_source /
              by_entry_type / preserved_audited_count / sample_ids /
              confirm_required + confirm_token)
            → 数据完全不动
        dry_run=False & affected_count <= bulk_op_require_confirm_above (默认 100)
            → 直接执行 BulkOpGuard.execute → ns CASCADE 删 → 204
        dry_run=False & affected_count > 阈值
            → confirm_token 缺/错  → 422 (含 expected_token + affected_count)
            → confirm_token 正确    → 执行删除 → 204

    breaking change: 旧 DELETE 不带 dry_run 默认走 preview, 不再直接 204; 项目
    处测试期接受. 前端配合 dry_run + confirm dialog 升级.
    """
    ns = await _get_namespace(db, ns_id)
    await assert_ns_owner(db, actor, ns_id)

    # ── BulkOpGuard preview 探影响范围 (永远 dry_run, 无副作用)
    preview_guard = BulkOperationGuard(
        op_name="namespace_delete",
        scope_filter={"namespace_id": ns_id},
        dry_run=True,
        actor_id=actor.id,
        reason=f"namespace delete ns_id={ns_id}",
        full_purge=True,
    )
    preview = await preview_guard.preview(db)
    expected_token = _compute_confirm_token(ns_id, preview.affected_count)
    confirm_required = preview.affected_count > settings.bulk_op_require_confirm_above

    # ── dry_run: 仅返报告
    if dry_run:
        return NamespaceDeletePreview(
            op_name=preview.op_name,
            affected_count=preview.affected_count,
            by_source=preview.by_source,
            by_entry_type=preview.by_entry_type,
            preserved_audited_count=preview.preserved_audited_count,
            sample_ids=preview.sample_ids,
            confirm_required=confirm_required,
            confirm_token=expected_token if confirm_required else None,
        )

    # ── 真删: 大批量必须 confirm_token (防误操作 + 防状态漂移)
    if confirm_required:
        if not confirm_token:
            raise HTTPException(
                422,
                detail={
                    "error": "confirm_token_required",
                    "affected_count": preview.affected_count,
                    "expected_token": expected_token,
                    "message": (
                        f"namespace 含 {preview.affected_count} 条 KE > "
                        f"{settings.bulk_op_require_confirm_above} 阈值, 需 confirm_token"
                    ),
                },
            )
        if confirm_token != expected_token:
            raise HTTPException(
                422,
                detail={
                    "error": "confirm_token_mismatch",
                    "affected_count": preview.affected_count,
                    "expected_token": expected_token,
                    "message": "confirm_token 不匹配 (规模可能已变化, 请重新 dry_run)",
                },
            )

    # ── BulkOpGuard 真执行 (KE + audit + ChromaDB best-effort)
    real_guard = BulkOperationGuard(
        op_name="namespace_delete",
        scope_filter={"namespace_id": ns_id},
        dry_run=False,
        actor_id=actor.id,
        reason=f"namespace delete ns_id={ns_id} confirmed",
        full_purge=True,
    )
    await real_guard.execute(db, slug=ns.slug)

    # ── 向量集合清理 + 命名空间本体删除 (CASCADE 清剩余 datasource)
    delete_knowledge_collection(ns.slug)

    # ── AC 自动机缓存清理 ──
    from app.knowledge.terminology_automaton import invalidate
    await invalidate(ns.id)

    # ── driver 连接池清理: CASCADE 删 datasources 行前先收集 ds_id,
    #    删除后逐个 evict, 防连接池/客户端残留持有 TCP 连接 ──
    ds_ids = list(
        (await db.scalars(select(DataSource.id).where(DataSource.namespace_id == ns_id))).all()
    )

    await db.delete(ns)
    await db.commit()

    # 主动清 registry 内存槽 (FK CASCADE 删 DB 行但不清内存, 不清理会导致配置泄漏)
    from app.engine.model_registry import registry
    registry.refresh_chat(None, namespace_id=ns_id)
    log.info("[namespace] deleted id=%s, registry chat slot cleared", ns_id)

    from app.engine.drivers import evict_datasource
    for did in ds_ids:
        await evict_datasource(did)

    return Response(status_code=204)


def _compute_confirm_token(ns_id: int, affected_count: int) -> str:
    """confirm_token 派生 — 防误操作 + 防状态漂移.

    非安全场景 (admin only, 即便预测也只是删自己有权限的 ns); 含 affected_count
    入参防"用户拿旧 dry_run 报告几小时后再确认", 期间 KE 数变化 → token 不匹配
    → 422 提示重新 dry_run.
    """
    return hashlib.sha256(f"{ns_id}:{affected_count}".encode()).hexdigest()[:16]


# ════════════════════════════════════════════
#  数据源
# ════════════════════════════════════════════

@router.get("/{ns_id}/datasources", response_model=list[DataSourceOut])
async def list_datasources(
    ns_id: int,
    _user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
):
    await _get_namespace(db, ns_id)
    result = await db.execute(
        select(DataSource).where(DataSource.namespace_id == ns_id)
    )
    return [DataSourceOut.from_orm_ds(ds) for ds in result.scalars().all()]


# ════════════════════════════════════════════
#  Phase 3 Task 3.1: 联动 API — terminology 编辑表单数据源
# ════════════════════════════════════════════

@router.get("/{ns_id}/databases")
async def get_namespace_databases(
    ns_id: int,
    _user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """列出 ns 下所有 DataSource — terminology 编辑表单一级下拉数据源."""
    rows = (await db.execute(
        select(DataSource).where(DataSource.namespace_id == ns_id)
    )).scalars().all()
    return {
        "databases": [
            {
                "database": ds.database,
                "db_type": ds.db_type,
                "datasource_id": ds.id,
                "host": ds.host,
            }
            for ds in rows
        ]
    }


@router.get("/{ns_id}/collections")
async def get_namespace_collections(
    ns_id: int,
    database: str,
    _user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """列出某 database 下的 collections/tables — terminology 编辑表单二级下拉数据源.

    mysql 与 mongodb 统一走 SchemaCanonicalObject 真相源 (按 db_type + database
    过滤 distinct target). 未知 DataSource (ns 下无该 database): db_type=null +
    collections=[] (前端容错).
    """
    from app.models.schema_canonical_object import SchemaCanonicalObject

    ds = (await db.execute(
        select(DataSource).where(
            DataSource.namespace_id == ns_id,
            DataSource.database == database,
        )
    )).scalar_one_or_none()

    if ds is None:
        return {"database": database, "db_type": None, "collections": []}

    rows = (await db.execute(
        select(SchemaCanonicalObject.target).where(
            SchemaCanonicalObject.namespace_id == ns_id,
            SchemaCanonicalObject.db_type == ds.db_type,
            SchemaCanonicalObject.database == database,
        ).distinct()
    )).all()
    colls = [r[0] for r in rows]

    return {"database": database, "db_type": ds.db_type, "collections": colls}


@router.post("/{ns_id}/datasources", response_model=DataSourceOut, status_code=201)
async def add_datasource(
    ns_id: int,
    body: DataSourceCreate,
    _user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
):
    await _get_namespace(db, ns_id)
    # 构造未落库的临时 ds (id=None) 用于连库验证 + 画像合成.
    # ⚠️ fetch_db_profile 走一次性临时连接, 不进 ds.id 缓存池 (id 为 None).
    ds = DataSource(
        namespace_id=ns_id,
        db_type=body.db_type,
        host=body.host,
        port=body.port,
        database=body.database,
        username=body.username,
        password=body.password,
        description=body.description,
        timezone=body.timezone,
    )
    # 连通才存: fetch_db_profile 的 connected 标志为连通判据 (降级安全, 永不抛).
    # 用 connected 而非 "version in profile": 受限账号能连但读不到 version 时不应误拒 (D4).
    driver = get_driver(body.db_type)
    profile = await driver.fetch_db_profile(ds)
    if not profile.get("connected"):
        # 连不上 (connected=False) → 拒绝, 不落库
        raise HTTPException(
            400,
            f"数据源连接失败, 无法访问 {body.host}:{body.port}/{body.database} — "
            f"请检查 host/port/库名/账号密码是否正确",
        )
    profile.pop("connected", None)  # 连通标志是建源决策用, 不落库 (profiled_at 已隐含)
    ds.db_profile_json = json.dumps(profile, ensure_ascii=False)
    db.add(ds)
    await db.commit()
    await db.refresh(ds)
    return DataSourceOut.from_orm_ds(ds)


@router.post("/{ns_id}/datasources/probe", response_model=DataSourceProbeOut)
async def probe_datasource(
    ns_id: int,
    body: DataSourceProbeIn,
    _user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
):
    """测试连通性 + 时区探测. 不落库. 返回 DataSourceProbeOut."""
    await _get_namespace(db, ns_id)
    # timezone 用 settings.app_timezone 占位: probe_connectivity 不读 ds.timezone, 不落库
    ds = DataSource(
        namespace_id=ns_id,
        db_type=body.db_type,
        host=body.host,
        port=body.port,
        database=body.database,
        username=body.username,
        password=body.password,
        description=body.description,
        timezone=settings.app_timezone,
    )
    driver = get_driver(body.db_type)
    result = await driver.probe_connectivity(ds)
    return DataSourceProbeOut(
        connected=result.connected,
        detected_timezone=result.detected_timezone,
        failure_reason=result.failure_reason,
    )


@router.post("/{ns_id}/datasources/{ds_id}/refresh-schema", response_model=SchemaRefreshResult)
async def refresh_schema(
    ns_id: int,
    ds_id: int,
    _user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
):
    """刷新数据源 schema — 数据库结构变更后调用"""
    ns = await _get_namespace(db, ns_id)
    ds = await db.get(DataSource, ds_id)
    if not ds or ds.namespace_id != ns_id:
        raise HTTPException(404, "数据源不存在")

    # 库级画像刷新 — db_type 中立 (MySQL/Mongo 皆可), 在 canonical 刷新之前先做,
    # 使 MongoDB 等暂不支持 canonical 刷新的引擎也能更新 db_profile (Spec §9 无 MySQL-only 限定).
    # 失败不阻断 (fetch_db_profile 本身降级安全, 连不上保留旧画像).
    try:
        driver = get_driver(ds.db_type)
        profile = await driver.fetch_db_profile(ds)
        if profile.get("connected"):  # 连通才更新, 连不上保留旧画像
            profile.pop("connected", None)  # 连通标志不落库 (与建源一致)
            ds.db_profile_json = json.dumps(profile, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001 — 画像刷新失败不影响 schema 刷新
        log.warning("[refresh_schema] db_profile 刷新失败 ds_id=%s: %s", ds_id, e)

    from app.engine.db_types import SQL_DB_TYPES

    if ds.db_type not in SQL_DB_TYPES:
        # MongoDB 等文档型: 暂无 canonical 刷新支持, 但库级画像已刷新, 持久化后返回
        await db.commit()
        return SchemaRefreshResult(
            success=False, message="当前 MongoDB 数据源暂不支持 Schema 刷新 (库级画像已刷新)",
        )

    try:
        from app.knowledge.schema_canonical import refresh_driver_canonicals
        count = await refresh_driver_canonicals(
            db, ns_id, ns.slug, db_type=ds.db_type, datasource_id=ds_id,
        )
        await db.commit()
        return SchemaRefreshResult(
            success=True,
            table_count=count,
            message=f"Schema 已刷新, 识别到 {count} 张表",
        )
    except Exception as e:
        return SchemaRefreshResult(success=False, message=f"Schema 刷新失败: {e}")


@router.delete("/{ns_id}/datasources/{ds_id}", status_code=204)
async def delete_datasource(
    ns_id: int,
    ds_id: int,
    _user: User = Depends(require_ns_manage),
    db: AsyncSession = Depends(get_db),
):
    """删除数据源 — 外键约束会级联删除相关 repo mappings"""
    await _get_namespace(db, ns_id)
    ds = await db.get(DataSource, ds_id)
    if not ds or ds.namespace_id != ns_id:
        raise HTTPException(404, "数据源不存在")
    # orphan hook: 在 CASCADE 删除前标记关联 candidate 为 orphaned
    from app.knowledge.candidate_cleanup import (
        cleanup_scos_for_datasource,
        orphan_candidates_for_datasource,
    )
    await orphan_candidates_for_datasource(db, ds_id)
    # 清理 promote 后的 SCO 真相源 (SCO 无 datasource_id, 不随外键级联删除)
    await cleanup_scos_for_datasource(db, ds)
    await db.delete(ds)
    await db.commit()

    # driver 连接池清理: 防 ds 删除后 pool/client 残留持有 TCP 连接
    from app.engine.drivers import evict_datasource
    await evict_datasource(ds_id)


# ════════════════════════════════════════════
#  P0 对话体验 — Readiness
# ════════════════════════════════════════════

@router.get("/{namespace_id}/readiness", response_model=ReadinessOut)
async def get_readiness(
    namespace_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """检查命名空间是否可问数。返回 ready 状态 + 阻塞原因."""
    # has_access
    has_access = True
    try:
        await assert_ns_access(db, user, namespace_id)
    except HTTPException:
        has_access = False

    # has_datasource
    ds_count = await db.scalar(
        select(func.count()).select_from(DataSource).where(
            DataSource.namespace_id == namespace_id,
        )
    )
    has_datasource = (ds_count or 0) > 0

    # has_chat_model: 该 namespace 专属 active CHAT 配置, 或全局兜底 active CHAT 配置
    # (与 model-config/check-ready 的 namespace OR 全局兜底 判定保持一致)
    chat_active = await db.scalar(
        select(func.count()).select_from(ModelConfig).where(
            ModelConfig.model_type == "CHAT",
            ModelConfig.is_active.is_(True),
            ModelConfig.is_deleted.is_(False),
            (ModelConfig.namespace_id == namespace_id)
            | (ModelConfig.namespace_id.is_(None)),
        )
    )
    has_chat_model = (chat_active or 0) > 0

    # has_embedding_key
    embedding_active = await db.scalar(
        select(func.count()).select_from(ModelConfig).where(
            ModelConfig.model_type == "EMBEDDING",
            ModelConfig.is_active.is_(True),
            ModelConfig.is_deleted.is_(False),
        )
    )
    has_embedding_key = (embedding_active or 0) > 0

    # has_valid_schema
    schema_count = await db.scalar(
        select(func.count()).select_from(SchemaCanonicalObject).where(
            SchemaCanonicalObject.namespace_id == namespace_id,
        )
    )
    has_valid_schema = (schema_count or 0) > 0

    ready = has_access and has_datasource and has_chat_model and has_valid_schema

    blockers: list[BlockerOut] = []
    if not has_access:
        blockers.append(BlockerOut(
            type="no_access",
            message="无权访问该空间",
            admin_action="",
            admin_route="",
            user_action="请联系管理员获取空间访问权限",
        ))
    if not has_datasource:
        blockers.append(BlockerOut(
            type="no_datasource",
            message="空间没有可用的数据源",
            admin_action="去添加数据源",
            admin_route=f"/namespaces/{namespace_id}",
            user_action="请联系管理员配置数据源",
        ))
    if not has_chat_model:
        blockers.append(BlockerOut(
            type="no_api_key",
            message="未配置可用的 Chat 模型 (该空间专属或全局兜底)",
            admin_action="去配置 API Key",
            admin_route="/model-management",
            user_action="请联系管理员配置模型凭证",
        ))
    if not has_embedding_key:
        blockers.append(BlockerOut(
            type="no_embedding_key",
            message="未配置全局默认 Embedding Key",
            admin_action="去配置 Embedding Key",
            admin_route="/model-management",
            user_action="请联系管理员配置 Embedding 模型凭证",
        ))
    if not has_valid_schema:
        blockers.append(BlockerOut(
            type="no_schema",
            message="Schema 尚未采集",
            admin_action="去采集 Schema",
            admin_route=f"/namespaces/{namespace_id}",
            user_action="请联系管理员完成 Schema 采集",
        ))

    return ReadinessOut(
        ready=ready,
        checks={
            "has_access": has_access,
            "has_datasource": has_datasource,
            "has_chat_model": has_chat_model,
            "has_embedding_key": has_embedding_key,
            "has_valid_schema": has_valid_schema,
        },
        blockers=blockers,
    )


# ════════════════════════════════════════════
#  工具函数
# ════════════════════════════════════════════

async def _get_namespace(db: AsyncSession, ns_id: int) -> Namespace:
    ns = await db.get(Namespace, ns_id)
    if not ns:
        raise HTTPException(404, "命名空间不存在")
    return ns
