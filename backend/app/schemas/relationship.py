"""Relationship entry pydantic schema — PATCH relationships 校验."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class RelationshipEntry(BaseModel):
    from_target: str
    from_field: str
    to_db_type: str = "mysql"
    to_database: str = ""
    to_target: str
    to_field: str
    relation_type: Literal["many_to_one", "one_to_many", "one_to_one"] = "many_to_one"
