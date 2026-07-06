"""probe_row_limit 配置字段 — 取代三驱动 probe 分支的 hardcode 10."""
from __future__ import annotations

from app.config import settings


def test_probe_row_limit_default_is_10():
    # 默认值保持原 hardcode 语义 (小探查 10 行), 但现在可经 IS_PROBE_ROW_LIMIT 覆盖
    assert settings.probe_row_limit == 10


def test_probe_row_limit_is_int():
    assert isinstance(settings.probe_row_limit, int)
