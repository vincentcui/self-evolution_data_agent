"""Task 2: timezone 列 + IANA 校验 + from_orm_ds 传递."""
import pytest
from app.models.namespace import DataSource
from app.schemas import DataSourceCreate, DataSourceOut


def test_timezone_column_default_notnull():
    """DataSource ORM 模型 timezone 列可构造且不丢失值."""
    ds = DataSource(db_type="mysql", host="h", port=3306, database="d",
                    username="u", password="p", timezone="Asia/Shanghai")
    assert ds.timezone == "Asia/Shanghai"


def test_create_rejects_invalid_iana():
    """DataSourceCreate 拒绝非法 IANA 时区名."""
    with pytest.raises(ValueError):
        DataSourceCreate(db_type="mysql", host="h", port=3306, database="d",
                         username="u", password="p", timezone="Not/A/Zone")


def test_create_accepts_valid_iana():
    """DataSourceCreate 接受合法 IANA 时区名."""
    ds = DataSourceCreate(db_type="mysql", host="h", port=3306, database="d",
                          username="u", password="p", timezone="America/New_York")
    assert ds.timezone == "America/New_York"


def test_create_rejects_china_tz_timezone():
    """DataSourceCreate 拒绝非 IANA 格式的时区."""
    with pytest.raises(ValueError):
        DataSourceCreate(db_type="mysql", host="h", port=3306, database="d",
                         username="u", password="p", timezone="CST")


def test_from_orm_ds_preserves_timezone():
    """from_orm_ds 手工构造器必须传递 timezone.

    from_orm_ds 绕过 from_attributes 自动映射逐字段拼装,
    新增 timezone 字段必须在此同步传递, 否则 add_datasource 返回值丢字段.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ds = DataSource(db_type="mysql", host="h", port=3306, database="d",
                    username="u", password="p", timezone="Asia/Shanghai")
    ds.id = 1  # 未持久化实例补 id/created_at, 满足 DataSourceOut 必填类型
    ds.description = ""  # ORM 实例 flush 前 description 为 None, 显式设空避免 Pydantic str 校验失败
    ds.db_profile_json = "{}"
    ds.created_at = datetime(2026, 1, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    out = DataSourceOut.from_orm_ds(ds)
    assert out.timezone == "Asia/Shanghai"
