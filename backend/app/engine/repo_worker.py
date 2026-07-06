"""
后台解析 Worker — asyncio.create_task 驱动
session-per-operation: 每次 DB 写操作独立 session, 长时 I/O 不占连接

═══════════════════════════════════════════════
 并发策略
═══════════════════════════════════════════════
Semaphore(4) 限制同时训练的 worker 数。排队等锁和
I/O 密集阶段（git clone / LLM 调用）不持有 DB 连接,
从根本上消除连接池耗尽问题。
"""

import asyncio
import time
import uuid

from app.db.metadata import async_session
from app.logging_config import get_logger, trace_id_var
from app.models import GitRepo, Namespace

log = get_logger("worker")

# ── 并发限制 — 全局 Semaphore ──
PARSE_CONCURRENCY = 4
_parse_semaphore = asyncio.Semaphore(PARSE_CONCURRENCY)

# ── 活跃 worker 注册表 ──
_active_workers: dict[str, asyncio.Task] = {}

# ════════════════════════════════════════════
#  全量重建清场注册表 (ns_id → 清场进行中)
#
#  清场是 worker 启动「之前」的阶段, 此时 repo.worker_id 仍为空,
#  靠 worker_id 无法感知。注册表填补这段可见性盲区:
#  batch_parse_repos(force) 同步标记 → list_repos 读取 → 前端轮询启动,
#  worker 起来后清标记, 进度条无缝接管。
# ════════════════════════════════════════════
_rebuilding_namespaces: set[int] = set()


def mark_rebuilding(ns_id: int) -> None:
    """标记 ns 进入全量清场窗口。须在派发后台 task 「之前」同步调用。"""
    _rebuilding_namespaces.add(ns_id)


def clear_rebuilding(ns_id: int) -> None:
    """清场结束 (worker 已启动 / 异常退出) 时清标记。"""
    _rebuilding_namespaces.discard(ns_id)


def is_rebuilding(ns_id: int) -> bool:
    """ns 是否处于全量清场窗口 (worker 尚未起来的中间态)。"""
    return ns_id in _rebuilding_namespaces


# ════════════════════════════════════════════
#  公共查询接口 (P1-19 A3: api 层走此函数, 不直读私有 dict)
# ════════════════════════════════════════════

def is_worker_running(worker_id: str) -> bool:
    """worker_id 是否有活跃 worker task."""
    return worker_id in _active_workers


def is_worker_done(worker_id: str) -> bool:
    """worker 已完成 (worker_id 不在注册表说明 finally 已清, 或 task .done())."""
    task = _active_workers.get(worker_id)
    return task is None or task.done()


async def _update_repo(repo_id: int, **fields) -> None:
    """短命 session — 更新 repo 字段后立即释放连接"""
    async with async_session() as db:
        repo = await db.get(GitRepo, repo_id)
        if repo:
            for k, v in fields.items():
                setattr(repo, k, v)
            await db.commit()


async def start_parse_worker(repo_id: int, ns_id: int) -> str:
    """
    启动后台解析 worker, 返回 worker_id

    Semaphore(4) 限流：任务立即创建但排队等锁，
    排队时 progress_message 显示"排队等待中..."
    """
    worker_id = str(uuid.uuid4())[:8]

    async def _run():
        # ── 切断父 context 污染: asyncio.create_task 会拷贝调用方 context,
        #    避免后台 worker 日志错误继承 HTTP 请求的 trace_id ──
        trace_id_var.set("-")

        from app.knowledge.trainer import run_training_pipeline_with_progress
        t0 = time.time()
        log.info("Worker 创建 worker_id=%s repo_id=%d ns_id=%d", worker_id, repo_id, ns_id)

        try:
            # ── Phase 1: 短命 session — 加载标量 + 标记排队 ──
            async with async_session() as db:
                repo = await db.get(GitRepo, repo_id)
                ns = await db.get(Namespace, ns_id)
                if not repo or not ns:
                    log.error("Worker 中止 worker_id=%s — repo 或 namespace 不存在", worker_id)
                    clear_rebuilding(ns_id)  # 未落库 worker_id, 清标记防全量清场悬挂
                    return

                # 提取标量, session 关闭后仍可用 (expire_on_commit=False)
                ns_slug = ns.slug
                repo_url = repo.url
                repo_branch = repo.branch
                repo_name = repo_url.rsplit("/", 1)[-1].removesuffix(".git")

                repo.worker_id = worker_id
                repo.parse_status = "parsing"
                repo.progress = 0
                repo.progress_message = "排队等待中..."
                await db.commit()
            # ← session 关闭, 连接归还

            # worker_id 已落库, 全量清场的可见性盲区到此结束 —
            # 移交 worker_id 接管前端轮询。clear 与落库同协程顺序执行,
            # 保证 clear 之后 worker_id 必已可见, 无时序缝隙。
            # (单 / 增量解析时 ns 无清场标记, discard 幂等 no-op)
            clear_rebuilding(ns_id)
        except Exception as e:
            log.error("Worker Phase1 失败 worker_id=%s repo_id=%d error=%s",
                      worker_id, repo_id, e, exc_info=True)
            clear_rebuilding(ns_id)  # 未落库 worker_id, 清标记防全量清场悬挂
            try:
                await _update_repo(
                    repo_id, parse_status="error", error_message=f"初始化失败: {e}",
                    worker_id="", progress=0, progress_message="",
                )
            except Exception:
                pass
            _active_workers.pop(worker_id, None)
            return

        # ── Phase 2: 等待并发槽位 (不持有连接) ──
        async with _parse_semaphore:
            log.info("Worker 获得槽位 %s worker_id=%s", repo_name, worker_id)
            await _update_repo(repo_id, progress_message="初始化...")

            async def on_progress(pct: int, msg: str):
                """进度回调 — 独立 session 写入 DB"""
                await _update_repo(repo_id, progress=pct, progress_message=msg)
                log.info("[%d%%] %s %s", pct, repo_name, msg)

            error_msg = ""
            try:
                # ── Phase 3: 训练管道 (标量参数, 内部自管 session) ──
                await run_training_pipeline_with_progress(
                    repo_id, ns_id, ns_slug, repo_url, repo_branch,
                    on_progress,
                )
                elapsed = time.time() - t0
                log.info("Worker 完成 %s worker_id=%s 耗时 %.1fs", repo_name, worker_id, elapsed)
            except asyncio.CancelledError:
                log.info("Worker 取消 %s worker_id=%s", repo_name, worker_id)
                error_msg = "用户取消"
            except Exception as e:
                log.error("Worker 异常 %s worker_id=%s 耗时 %.1fs",
                          repo_name, worker_id, time.time() - t0, exc_info=True)
                error_msg = str(e)[:500]
            finally:
                # ── 单次原子操作: 清理 worker 标记 + 兜底状态修正 ──
                try:
                    async with async_session() as db:
                        repo = await db.get(GitRepo, repo_id)
                        if repo:
                            repo.worker_id = ""
                            repo.progress_message = ""
                            if error_msg:
                                repo.parse_status = "error"
                                repo.error_message = error_msg
                                repo.progress = 0
                            elif repo.parse_status not in ("parsed", "error"):
                                # 管道未正常完成, 兜底设为 error
                                repo.parse_status = "error"
                                repo.error_message = "Worker 异常终止"
                                repo.progress = 0
                            await db.commit()
                except Exception:
                    pass
                _active_workers.pop(worker_id, None)

    task = asyncio.create_task(_run())
    _active_workers[worker_id] = task
    return worker_id


def cancel_worker(worker_id: str) -> bool:
    """取消活跃 worker"""
    task = _active_workers.get(worker_id)
    if task and not task.done():
        task.cancel()
        _active_workers.pop(worker_id, None)
        log.info("Worker 取消请求 worker_id=%s", worker_id)
        return True
    return False


def get_active_workers() -> dict[str, bool]:
    """返回所有活跃 worker 状态"""
    return {wid: not t.done() for wid, t in _active_workers.items()}
