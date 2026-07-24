"""Task 4b — instance_alias schema 加 db_type 必填.

validate_instance_alias_payload 缺 db_type 报错; 非法 db_type 报错; 合法通过.
"""
import pytest

from app.knowledge.instance_alias_intake import (
    InstanceAliasValidationError,
    validate_instance_alias_payload,
)


def test_instance_alias_requires_db_type():
    with pytest.raises(InstanceAliasValidationError):
        validate_instance_alias_payload({
            "alias": "我们的 top vendor",
            "target_collection": "vendors",
            "target_database": "shop",
            "target_id": "v_042",
            # 缺 db_type
        })


def test_instance_alias_rejects_invalid_db_type():
    with pytest.raises(InstanceAliasValidationError):
        validate_instance_alias_payload({
            "alias": "我们的 top vendor", "target_collection": "vendors",
            "target_database": "shop", "target_id": "v_042", "db_type": "redis",
        })


def test_instance_alias_accepts_valid_db_type():
    p = validate_instance_alias_payload({
        "alias": "我们的 top vendor", "target_collection": "vendors",
        "target_database": "shop", "target_id": "v_042", "db_type": "mongodb",
    })
    assert p["db_type"] == "mongodb"
