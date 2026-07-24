"""instance_alias 类型 payload schema 校验.

instance_alias 用途: 把"用户口语化别名"映射到"某条具体记录的 ID",
让 LLM 召回后直接拿 target_id 调 execute_query 精确查该记录, 跳过字段探查.

不进 AC 自动机, 仅靠 RAG 向量召回.
"""
from __future__ import annotations

from typing import TypedDict

from app.engine.db_types import SUPPORTED_DB_TYPES


class InstanceAliasPayload(TypedDict):
    """instance_alias entry 的 payload schema.

    validate_instance_alias_payload 保证所有字段都有值.
    """

    alias: str  # 用户问题里的简称原词
    canonical_name: str  # 记录的全名 (供审核者识别)
    target_collection: str  # 落库集合名
    target_database: str  # 数据库名
    target_id: str  # 记录的 _id 或唯一键值
    id_field: str  # 默认 "_id"
    db_type: str  # 该记录所在库的数据库类型 (executor 据此选 SQL/Mongo 语法)


class InstanceAliasValidationError(ValueError):
    """payload 校验失败."""


_REQUIRED_FIELDS = ("alias", "target_collection", "target_database", "target_id", "db_type")
_ALIAS_MAX_LEN = 50


def validate_instance_alias_payload(payload: dict) -> InstanceAliasPayload:
    """校验并归一化 payload. 失败抛 InstanceAliasValidationError.

    必填: alias / target_collection / target_database / target_id
    可选: canonical_name (推荐填) / id_field (默认 _id)
    """
    if not isinstance(payload, dict):
        raise InstanceAliasValidationError(
            f"payload 必须是 dict, 收到 {type(payload).__name__}"
        )

    missing = [f for f in _REQUIRED_FIELDS if not payload.get(f)]
    if missing:
        raise InstanceAliasValidationError(
            f"instance_alias payload 缺必填字段: {missing}"
        )

    alias = str(payload["alias"]).strip()
    if not alias:
        raise InstanceAliasValidationError("alias 不能为空白")
    if len(alias) > _ALIAS_MAX_LEN:
        raise InstanceAliasValidationError(
            f"alias 长度 {len(alias)} 超上限 {_ALIAS_MAX_LEN}"
        )

    db_type = str(payload.get("db_type", "")).strip()
    if db_type not in SUPPORTED_DB_TYPES:
        raise InstanceAliasValidationError(
            f"db_type 非法: {db_type!r}, 合法值 {sorted(SUPPORTED_DB_TYPES)}"
        )

    return InstanceAliasPayload(
        alias=alias,
        canonical_name=str(payload.get("canonical_name") or "").strip(),
        target_collection=str(payload["target_collection"]).strip(),
        target_database=str(payload["target_database"]).strip(),
        target_id=str(payload["target_id"]).strip(),
        id_field=str(payload.get("id_field") or "_id").strip(),
        db_type=db_type,
    )
