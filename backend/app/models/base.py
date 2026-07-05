"""ORM 基类 + 项目时间约定唯一入口"""

from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

# ════════════════════════════════════════════
#  项目时间约定 (唯一真相源)
#
#  所有表列: TIMESTAMP WITHOUT TIME ZONE, 存 IS_APP_TIMEZONE naive 本地时间。
#  DB 层: server_default = LOCAL_NOW (now() AT TIME ZONE IS_APP_TIMEZONE)。
#  Python 层: 凡需要"当前时间"的地方, 唯一入口 = local_now()。
#  禁止: datetime.now() / datetime.utcnow() / datetime.now(timezone.utc) 裸调。
# ════════════════════════════════════════════

_APP_TZ = ZoneInfo(settings.app_timezone)
LOCAL_NOW = text("(now() AT TIME ZONE '{}')".format(settings.app_timezone))


def local_now() -> datetime:
    """返回 IS_APP_TIMEZONE naive 本地时间 — 与 DB LOCAL_NOW server_default 语义一致。

    用途:
      - 模型 created_at/updated_at 的 `default=local_now`
      - 业务代码中任何需要"当前时间戳"的场景
      - 替代 datetime.now() (系统 locale 不可控) / datetime.utcnow() (已弃用)

    返回 naive datetime (无 tzinfo), 匹配 TIMESTAMP WITHOUT TIME ZONE 列类型。
    """
    return datetime.now(_APP_TZ).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass
