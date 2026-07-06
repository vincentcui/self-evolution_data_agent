"""数据源驱动层 — 多态 dispatch.

详见 docs/superpowers/specs/2026-05-09-agent-loop-multi-driver/04-driver-abstraction.md
"""
from __future__ import annotations

from app.engine.drivers._exceptions import UnsupportedDataSourceTypeError
from app.engine.drivers.base import DataSourceDriver
from app.engine.drivers.mongo import MongoDriver
from app.engine.drivers.mysql import MySQLDriver
from app.engine.drivers.oracle import OracleDriver

DRIVERS: dict[str, DataSourceDriver] = {
    "mysql": MySQLDriver(),
    "mongodb": MongoDriver(),
    "oracle": OracleDriver(),
}

# ── 启动期硬约束: 每个 driver 必须声明 paradigm ──
# 新增 driver 忘了填 paradigm → 进程启动即 crash, 不给静默机会.
for _t, _d in DRIVERS.items():
    _p = getattr(_d, "paradigm", None)
    assert _p is not None, (
        f"driver '{_t}' 未声明 paradigm — "
        f"新增 driver 必须填 paradigm 字段 ({list(DRIVERS.keys())})"
    )
    assert _p in ("relational", "document"), (
        f"driver '{_t}'.paradigm={_p!r} 不合法, "
        f"paradigm 必须是 'relational' 或 'document'"
    )


def get_driver(db_type: str) -> DataSourceDriver:
    """按 db_type 获取 driver 单例. 未注册则抛 UnsupportedDataSourceType."""
    if db_type not in DRIVERS:
        raise UnsupportedDataSourceTypeError(
            f"未支持的 db_type: {db_type}",
            suggestion=f"已注册: {sorted(DRIVERS.keys())}",
        )
    return DRIVERS[db_type]


async def evict_datasource(ds_id: int) -> None:
    """DataSource 删除/变更时清理各 driver 中按 ds_id 缓存的连接池/客户端.

    遍历所有已注册 driver, 优先调用 invalidate_pool, 否则调 invalidate_client.
    一个 ds 只属于一种 db_type, 对其他 driver 的清理是 no-op pop (幂等安全).
    防止 CASCADE 删 datasources 行后, driver 内 _pools/_clients 残留
    持有 TCP 连接直到进程重启.
    新增 driver 时无需修改此函数, 只需在 DRIVERS 注册.
    """
    for driver in DRIVERS.values():
        if hasattr(driver, "invalidate_pool"):
            await driver.invalidate_pool(ds_id)  # type: ignore[attr-defined]
        elif hasattr(driver, "invalidate_client"):
            await driver.invalidate_client(ds_id)  # type: ignore[attr-defined]


async def shutdown_all_drivers():
    """uvicorn shutdown hook 调用, 优雅关闭所有 driver 连接池."""
    for driver in DRIVERS.values():
        if hasattr(driver, "close_all"):
            await driver.close_all()  # type: ignore[attr-defined]
