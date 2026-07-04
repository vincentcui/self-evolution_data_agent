"""
Self-Evolution Data Agent — FastAPI 入口
"""

import json
import os
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import (
    agent_traces as agent_traces_api,
)
from app.api import (
    audit as audit_api,
)
from app.api import (
    auth as auth_api,
)
from app.api import (
    enum_dictionary as enum_dictionary_api,
)
from app.api import (
    feedback as feedback_api,
)
from app.api import (
    extraction_failure as extraction_failure_api,
)
from app.api import (
    history,
    knowledge,
    namespace,
    query,
    session,
    share,
)
from app.api import (
    model_config as model_config_api,
)
from app.api import (
    profile as profile_api,
)
from app.api import (
    schema_canonical as schema_canonical_api,
)
from app.api import (
    schema_canonical_v2 as schema_canonical_v2_api,
)
from app.api import (
    terminology_conflict as terminology_conflict_api,
)
from app.api import (
    terminology_refresh as terminology_refresh_api,
)
from app.api import (
    users as users_api,
)
from app.auth import hash_password
from app.db.metadata import async_session, engine, get_db
from app.db.schema_migrations import run_all as run_schema_migrations
from app.knowledge.equivalence import (
    checkers as _equivalence_checkers,  # noqa: F401 — side-effect register
)
from app.logging_config import get_logger, setup_logging, trace_id_var
from app.models.base import Base
from app.models.user import User
from app.tracing import get_client as get_langfuse_client

log = get_logger("main")
access_log = get_logger("access")


async def _init_admin():
    """首次启动创建默认超级管理员 admin (密码取 IS_DEFAULT_ADMIN_PASSWORD)。"""
    from app.auth import ROLE_SUPER_ADMIN
    from app.config import settings
    async with async_session() as db:
        result = await db.execute(select(User).where(User.username == "admin"))
        if result.scalars().first():
            return
        admin = User(
            username="admin",
            password_hash=hash_password(settings.default_admin_password),
            role=ROLE_SUPER_ADMIN,
        )
        db.add(admin)
        await db.commit()


async def _migrate_git_repo_status():
    """列迁移 + Phase 3 新列 + knowledge_entries.repo_id (兼容已有 PostgreSQL)"""
    async with async_session() as db:
        # 检测现有列
        result = await db.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'git_repos'"
        ))
        columns = {row[0] for row in result.fetchall()}

        # 1. status → parse_status 重命名
        if "status" in columns and "parse_status" not in columns:
            await db.execute(text("ALTER TABLE git_repos RENAME COLUMN status TO parse_status"))
            await db.commit()

        # 2. Phase 3 新列添加
        if "worker_id" not in columns:
            await db.execute(text(
                "ALTER TABLE git_repos ADD COLUMN worker_id VARCHAR(36) DEFAULT ''"
            ))
            await db.commit()
        if "progress" not in columns:
            await db.execute(text(
                "ALTER TABLE git_repos ADD COLUMN progress INTEGER DEFAULT 0"
            ))
            await db.commit()
        if "progress_message" not in columns:
            await db.execute(text(
                "ALTER TABLE git_repos ADD COLUMN progress_message TEXT DEFAULT ''"
            ))
            await db.commit()

        # 3. knowledge_entries.repo_id 列添加
        ke_result = await db.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'knowledge_entries'"
        ))
        ke_cols = {row[0] for row in ke_result.fetchall()}
        if "repo_id" not in ke_cols:
            await db.execute(text(
                "ALTER TABLE knowledge_entries ADD COLUMN repo_id INTEGER"
                " REFERENCES git_repos(id) ON DELETE SET NULL"
            ))
            await db.commit()


async def _migrate_query_history():
    """query_history 新增 result_snapshot 列 (兼容已有 PostgreSQL)"""
    async with async_session() as db:
        result = await db.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'query_history'"
        ))
        columns = {row[0] for row in result.fetchall()}

        if "result_snapshot" not in columns:
            await db.execute(text(
                "ALTER TABLE query_history ADD COLUMN result_snapshot TEXT DEFAULT ''"
            ))
            await db.commit()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # ── 启动: 日志 → 数据目录 → 表 → 迁移 → 管理员 ──
    setup_logging()
    log.info("Self-Evolution Data Agent 启动中...")

    os.makedirs("./data", exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("数据库表创建/检查完成")

    await run_schema_migrations(engine)
    log.info("[main] schema migrations completed")

    await _migrate_git_repo_status()
    await _migrate_query_history()
    log.info("数据库迁移检查完成")

    await _init_admin()
    log.info("管理员账户就绪")

    # ── 模型注册中心：从 DB 恢复激活配置（热切换状态重启不丢失）──
    from app.engine.model_registry import registry as _model_registry
    await _model_registry.load_from_db()

    # ── Langfuse 追踪初始化 ──
    from app.tracing import init_langfuse
    init_langfuse()

    # ── AC 自动机初始化 (术语精确匹配) ──
    from app.knowledge.terminology_automaton import init_all_automatons
    async with async_session() as db:
        await init_all_automatons(db)

    # ── P3: pending_clarifications TTL 清理后台任务 ──
    import asyncio as _asyncio

    from app.engine.pending_cleanup import pending_cleanup_loop
    _cleanup_task = _asyncio.create_task(
        pending_cleanup_loop(), name="pending_cleanup",
    )

    # ── Stage 3 Task 8: proposed 自动过期后台任务 ──
    from app.knowledge.auto_expire import proposed_auto_expire_loop
    _expire_task = _asyncio.create_task(
        proposed_auto_expire_loop(), name="proposed_auto_expire",
    )

    # ── Phase 2 Plan 05: enum sync worker ──
    from app.knowledge.enum_sync import enum_sync_loop
    _enum_sync_task = _asyncio.create_task(
        enum_sync_loop(async_session), name="enum_sync",
    )

    # ── Stage 2 抓手 E: agent_traces cleanup ──
    from app.jobs.agent_trace_cleanup import cleanup_loop as _agent_trace_cleanup_loop
    _agent_trace_cleanup_task = _asyncio.create_task(
        _agent_trace_cleanup_loop(), name="agent_trace_cleanup",
    )

    # ── Stage 2 抓手 B: 知识衰减 cron ──
    from app.jobs.knowledge_decay import decay_loop
    _decay_task = _asyncio.create_task(
        decay_loop(), name="knowledge_decay",
    )

    log.info("服务启动完成 port=8001")
    yield
    # ── 关闭 ──
    log.info("服务关闭中...")

    _cleanup_task.cancel()
    try:
        await _cleanup_task
    except (_asyncio.CancelledError, Exception):
        pass

    _expire_task.cancel()
    try:
        await _expire_task
    except (_asyncio.CancelledError, Exception):
        pass

    _enum_sync_task.cancel()
    try:
        await _enum_sync_task
    except (_asyncio.CancelledError, Exception):
        pass

    _agent_trace_cleanup_task.cancel()
    try:
        await _agent_trace_cleanup_task
    except (_asyncio.CancelledError, Exception):
        pass

    _decay_task.cancel()
    try:
        await _decay_task
    except (_asyncio.CancelledError, Exception):
        pass

    from app.tracing import shutdown_langfuse
    shutdown_langfuse()
    await engine.dispose()


app = FastAPI(
    title="Self-Evolution Data Agent",
    version="0.1.0",
    lifespan=lifespan,
)

# ── CORS ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 访问日志中间件 — 记录每个请求的完整生命周期 ──
# 格式: METHOD /path status=200 1.23ms query={} body=... resp=...

# 高频轮询路径 — 只记录非 200 响应, 避免日志洪水
_QUIET_PATHS = {"/api/health"}
_QUIET_SUFFIXES = ("/progress",)

# Body / Response 截断上限 (字符) - 仅日志格式, 非业务阈值
_MAX_BODY_LOG = 1024
_MAX_RESP_LOG = 512
_SENSITIVE_LOG_FIELDS = {
    "api_key",
    "password",
    "proxy_password",
    "refresh_token",
    "secret",
    "token",
}
_SENSITIVE_BODY_PREFIXES = ("/api/model-config",)


def _truncate(s: str, limit: int) -> str:
    return s if len(s) <= limit else s[:limit] + f"...({len(s)})"


def _redact_json_value(value):
    if isinstance(value, dict):
        return {
            key: "***REDACTED***"
            if str(key).lower() in _SENSITIVE_LOG_FIELDS
            else _redact_json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_json_value(item) for item in value]
    return value


def _redact_request_body_for_log(path: str, body: str) -> str:
    if not body:
        return body
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        if path.startswith(_SENSITIVE_BODY_PREFIXES):
            return "<redacted>"
        return body
    return json.dumps(_redact_json_value(parsed), ensure_ascii=False)


@app.middleware("http")
async def access_log_middleware(request: Request, call_next):
    # SSE 端点必须绕开 BaseHTTPMiddleware: Starlette `call_next` 会等下游响应完结才返回,
    # 把流式推送压成一次性返回. LLM 全程由 langfuse @observe 独立观测, 无需在此记日志.
    if request.url.path == "/api/query/stream":
        return await call_next(request)
    t0 = time.time()
    path = request.url.path
    method = request.method
    query_str = str(request.query_params) if request.query_params else ""

    # ══════════════════════════════════════════════════════════
    #  Langfuse trace setup — 业务 API 起 root span
    # ══════════════════════════════════════════════════════════
    # /api/health 与非 /api/* 不起 trace, 减少 langfuse 噪音
    should_trace = path.startswith("/api/") and path != "/api/health"
    lf = get_langfuse_client() if should_trace else None
    trace_cm = None
    trace_id = "-"

    if lf is not None:
        try:
            trace_cm = lf.start_as_current_observation(
                name=f"{method} {path}", as_type="span"
            )
            trace_cm.__enter__()
            trace_id = lf.get_current_trace_id() or "-"
        except Exception:
            trace_cm = None  # langfuse 故障降级, 不影响主流程

    token = trace_id_var.set(trace_id)
    request.state.trace_id = trace_id

    try:
        # ── 读取请求体 (消费后复原) ──
        req_body = ""
        if method in ("POST", "PUT", "PATCH"):
            try:
                raw = await request.body()
                req_body = _redact_request_body_for_log(
                    path,
                    raw.decode("utf-8", errors="replace"),
                )
            except Exception:
                req_body = "<read-error>"

        # ── 调用下游 ──
        try:
            response = await call_next(request)
        except Exception:
            elapsed = round((time.time() - t0) * 1000, 1)
            access_log.error(
                "%s %s status=500 %.1fms query=%s body=%s [unhandled exception]",
                method, path, elapsed, query_str, _truncate(req_body, _MAX_BODY_LOG),
                exc_info=True,
            )
            return JSONResponse(status_code=500, content={"detail": "内部错误"})

        elapsed = round((time.time() - t0) * 1000, 1)
        status = response.status_code

        # ── 高频路径: 仅异常时记录 ──
        is_quiet = path in _QUIET_PATHS or path.endswith(_QUIET_SUFFIXES)
        if is_quiet and status < 400:
            if trace_id != "-":
                response.headers["X-Trace-Id"] = trace_id
            return response

        # ── 流式响应 (SSE) bypass: 消费 body_iterator 会缓冲整流, 破坏推送语义.
        #    LLM 交互全程由 langfuse @observe 独立观测, 无需在此再记响应体.
        if response.media_type == "text/event-stream":
            if trace_id != "-":
                response.headers["X-Trace-Id"] = trace_id
            access_log.info(
                "%s %s status=%d %.1fms streaming", method, path, status, elapsed,
            )
            return response

        # ── 读取响应体 (StreamingResponse 需重建) ──
        resp_body = ""
        try:
            body_chunks = []
            async for chunk in response.body_iterator:
                body_chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
            resp_bytes = b"".join(body_chunks)
            resp_body = resp_bytes.decode("utf-8", errors="replace")
            # 重建 Response (原始 StreamingResponse 已被消费)
            response = Response(
                content=resp_bytes,
                status_code=status,
                headers=dict(response.headers),
                media_type=response.media_type,
            )
        except Exception:
            resp_body = "<read-error>"

        # ── 注入 trace_id 到 response header ──
        if trace_id != "-":
            response.headers["X-Trace-Id"] = trace_id

        # ── 拼装日志 ──
        parts = [f"{method} {path} status={status} {elapsed}ms"]
        if query_str:
            parts.append(f"query={query_str}")
        if req_body:
            parts.append(f"body={_truncate(req_body, _MAX_BODY_LOG)}")
        if resp_body:
            parts.append(f"resp={_truncate(resp_body, _MAX_RESP_LOG)}")

        msg = " | ".join(parts)
        if status >= 500:
            access_log.error(msg)
        elif status >= 400:
            access_log.warning(msg)
        else:
            access_log.info(msg)

        return response
    finally:
        if trace_cm is not None:
            try:
                trace_cm.__exit__(None, None, None)
            except Exception:
                pass
        trace_id_var.reset(token)


# ── 路由注册 ──
app.include_router(auth_api.router)
app.include_router(users_api.router)
app.include_router(query.router)
app.include_router(namespace.router)
app.include_router(knowledge.router)
app.include_router(history.router)
app.include_router(session.router)
app.include_router(share.router)
app.include_router(audit_api.router)
app.include_router(terminology_conflict_api.router)

app.include_router(terminology_refresh_api.router)

# Phase 2: EnumDictionary CRUD
app.include_router(enum_dictionary_api.router)

# Extraction failure log API
app.include_router(extraction_failure_api.router)

# Stage 2 抓手 E: agent_traces API
app.include_router(agent_traces_api.router)

# Stage 2: 通用 schema canonical API
# Phase 1: schema canonical v2 (promote / conflicts / candidates / evidence / etc.)
# 注: v2 必须在 v1 之前注册, 因为 v1 有 /{sco_id} 路径参数会吞掉 /conflicts 等路径
app.include_router(schema_canonical_v2_api.router)
app.include_router(schema_canonical_api.router)

# agentic-repo-extractor: Profile CRUD
app.include_router(profile_api.router)

# model-management: 模型配置 CRUD + 激活 + 测试连接
app.include_router(model_config_api.router)

# P0 对话体验: 答案反馈
app.include_router(feedback_api.router)


@app.get("/api/health")
async def health(db: AsyncSession = Depends(get_db)):
    """探 DB 可达性 — compose healthcheck / depends_on 的真就绪门.

    恒返 200 是健康剧场: DB 断连时编排层仍误判就绪。此处经注入 session
    SELECT 1 实探, 失败返 503 让 healthcheck 真实失败。
    经 Depends(get_db) 而非直连 async_session(): 复用 DI 接缝, 测试可
    经 dependency_overrides[get_db] 注入真实/抛错 session (见 test_health_endpoint)。
    """
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:  # DB 不可达 / 连接池耗尽 / 网络分区
        log.warning("health check db probe failed: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "db": "down"},
        )
    return {"status": "ok", "db": "ok"}
