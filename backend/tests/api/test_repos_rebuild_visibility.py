"""全量清场可见性契约 — _rebuilding_namespaces registry + list_repos.batch_status.

╔══════════════════════════════════════════════════════════════════════════╗
║  背景 (bug):                                                              ║
║    全量解析 force=True 改成两阶段异步 (先返回 → 后台清场 60s → 启 worker).║
║    清场阶段 repo.worker_id 仍为空, 前端轮询启动条件                       ║
║    (worker_id || batch_status.active) 在那一拍全 false → 轮询不启动 →     ║
║    60s 后 worker 起来时无人轮询 → 进度条永不出现, 体感按钮无效.           ║
║                                                                          ║
║  修复: batch_parse_repos 同步 mark_rebuilding(ns) 于 create_task 之前,    ║
║    list_repos 据 registry 回填 batch_status.active, worker 落库 worker_id ║
║    后 clear_rebuilding 无缝交接 (worker_id 接管可见性).                   ║
║                                                                          ║
║  本测锁住: registry 状态机不变量 + list_repos 可见性契约 + ns 隔离.       ║
╚══════════════════════════════════════════════════════════════════════════╝
"""
import pytest
import pytest_asyncio

from app.engine.repo_worker import (
    clear_rebuilding,
    is_rebuilding,
    mark_rebuilding,
)
from app.models.namespace import Namespace


# ════════════════════════════════════════════
#  Registry 状态机 — 纯单元 (无 DB / HTTP, 同步)
# ════════════════════════════════════════════

class TestRebuildRegistry:
    def test_mark_then_is_rebuilding(self):
        ns_id = 991001
        clear_rebuilding(ns_id)  # 防御: 其他测试残留
        assert is_rebuilding(ns_id) is False
        mark_rebuilding(ns_id)
        assert is_rebuilding(ns_id) is True
        clear_rebuilding(ns_id)
        assert is_rebuilding(ns_id) is False

    def test_clear_is_idempotent(self):
        """未标记时 clear 不抛 — discard 幂等, 兜底路径反复清安全。"""
        ns_id = 991002
        clear_rebuilding(ns_id)
        clear_rebuilding(ns_id)  # 二次清不报错
        assert is_rebuilding(ns_id) is False

    def test_namespace_isolation(self):
        """A 清场不影响 B — registry 按 ns_id 隔离。"""
        a, b = 991003, 991004
        clear_rebuilding(a)
        clear_rebuilding(b)
        mark_rebuilding(a)
        assert is_rebuilding(a) is True
        assert is_rebuilding(b) is False
        clear_rebuilding(a)


# ════════════════════════════════════════════
#  list_repos 可见性契约 — L2 (真实路由 + RBAC + savepoint db)
# ════════════════════════════════════════════

@pytest_asyncio.fixture
async def client(make_client):
    return await make_client(role="super_admin", user_id=1, username="admin")


@pytest_asyncio.fixture
async def ns_id(db) -> int:
    ns = Namespace(name="rebuild-vis", slug="rebuild-vis-991", created_by=1)
    db.add(ns)
    await db.commit()
    await db.refresh(ns)
    yield ns.id
    clear_rebuilding(ns.id)  # 防泄漏到其他测试


class TestListReposBatchStatus:
    """L2 契约 — 真实路由 + RBAC + savepoint db。"""

    pytestmark = pytest.mark.asyncio

    async def test_no_rebuild_batch_status_null(self, client, ns_id):
        """未清场: batch_status 为 null (基线, 不误报 active)。"""
        clear_rebuilding(ns_id)
        resp = await client.get(f"/api/namespaces/{ns_id}/repos")
        assert resp.status_code == 200
        assert resp.json()["batch_status"] is None

    async def test_rebuilding_exposes_active(self, client, ns_id):
        """清场窗口: list_repos 必须回 batch_status.active=true —
        这正是前端轮询启动 (worker_id || batch_status.active) 的钩子,
        修复前此处恒为 null, 轮询永不启动。"""
        mark_rebuilding(ns_id)
        resp = await client.get(f"/api/namespaces/{ns_id}/repos")
        assert resp.status_code == 200
        bs = resp.json()["batch_status"]
        assert bs is not None
        assert bs["active"] is True
        assert bs["message"]  # 有文案驱动前端横幅

    async def test_clear_restores_null(self, client, ns_id):
        """worker 落库 worker_id 后 clear → batch_status 回 null,
        交接给 worker_id 接管 (本测只验 registry 侧, 无盲窗)。"""
        mark_rebuilding(ns_id)
        clear_rebuilding(ns_id)
        resp = await client.get(f"/api/namespaces/{ns_id}/repos")
        assert resp.status_code == 200
        assert resp.json()["batch_status"] is None
