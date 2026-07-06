"""DataSourceCreate 收 description; DataSourceOut 出 description + db_profile."""
import pytest
from pydantic import ValidationError

from app.schemas import DataSourceCreate, DataSourceOut


def test_create_accepts_description_optional():
    """description 非必填, 缺省为空串."""
    c = DataSourceCreate(
        db_type="mysql", host="h", port=3306,
        database="d", username="u", password="p",
        timezone="Asia/Shanghai",
    )
    assert c.description == ""
    c2 = DataSourceCreate(
        db_type="mysql", host="h", port=3306,
        database="d", username="u", password="p", description="电力库",
        timezone="Asia/Shanghai",
    )
    assert c2.description == "电力库"


def test_out_exposes_description_and_profile_not_password():
    """DataSourceOut 暴露 description + db_profile (dict), 不暴露 password."""
    fields = set(DataSourceOut.model_fields.keys())
    assert "description" in fields
    assert "db_profile" in fields
    assert "password" not in fields


def test_create_oracle_db_type_accepted():
    """db_type=oracle 应通过 DataSourceCreate 校验."""
    c = DataSourceCreate(
        db_type="oracle", host="db.example.com", port=1521,
        database="orclpdb", username="hr", password="p1",
        timezone="Asia/Shanghai",
    )
    assert c.db_type == "oracle"
    assert c.port == 1521


def test_create_unsupported_db_type_rejected():
    """不支持的 db_type 应被 DataSourceCreate 拒绝."""
    with pytest.raises(ValidationError):
        DataSourceCreate(
            db_type="postgresql", host="h", port=5432,
            database="d", username="u", password="p1",
            timezone="Asia/Shanghai",
        )
