"""TDD: IS_APP_TIMEZONE 配置化 local_now/LOCAL_NOW — GREEN phase"""

from app.models.base import local_now


def test_local_now_uses_app_timezone(monkeypatch):
    monkeypatch.setattr("app.models.base.settings.app_timezone", "Asia/Singapore")
    # 重新初始化 _APP_TZ (模块级常量, 需 reload 或改函数内读)
    import importlib
    import app.models.base

    importlib.reload(app.models.base)
    from app.models.base import local_now
    from zoneinfo import ZoneInfo
    from datetime import datetime

    n = local_now()  # naive, Singapore local (UTC+8)
    # 对比也用 naive Singapore local — local_now 返回 naive, 不能与 aware 相减
    ref = datetime.now(ZoneInfo("Asia/Singapore")).replace(tzinfo=None)
    assert abs((n - ref).total_seconds()) < 60  # 同分钟内


def test_local_now_returns_naive():
    n = local_now()
    assert n.tzinfo is None  # naive, 匹配 TIMESTAMP WITHOUT TIME ZONE
