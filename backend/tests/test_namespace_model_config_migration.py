"""测试 model_configs 表新增 namespace_id 列的迁移."""
import pytest
from sqlalchemy import inspect, text


@pytest.mark.asyncio
async def test_model_configs_has_namespace_id_column(db):
    """model_configs 表应有 namespace_id 列, nullable, FK → namespaces.id."""
    cols = await db.execute(text(
        "SELECT column_name, is_nullable FROM information_schema.columns "
        "WHERE table_name='model_configs' AND column_name='namespace_id'"
    ))
    row = cols.fetchone()
    assert row is not None, "namespace_id 列不存在"
    assert row[1] == "YES", "namespace_id 应为 nullable"


@pytest.mark.asyncio
async def test_existing_rows_namespace_id_is_null(db):
    """存量行 namespace_id 应为 NULL."""
    result = await db.execute(text(
        "SELECT COUNT(*) FROM model_configs WHERE namespace_id IS NOT NULL"
    ))
    assert result.scalar() == 0


@pytest.mark.asyncio
async def test_unique_index_rebuilt_on_model_type_and_namespace_id(db):
    """唯一索引应从 (model_type) 重建为 (model_type, namespace_id).

    验证方式：查询 pg_indexes 断言 indexdef 含 (model_type, namespace_id)。
    若索引仍为旧结构（仅 model_type），安装第二个 namespace CHAT 时将违反唯一约束。
    """
    row = await db.execute(text(
        "SELECT indexdef FROM pg_indexes "
        "WHERE indexname = 'uq_model_configs_one_active_per_type'"
    ))
    indexdef = row.scalar()
    assert indexdef is not None, "唯一索引 uq_model_configs_one_active_per_type 不存在"
    assert "model_type" in indexdef and "namespace_id" in indexdef, (
        f"索引结构不包含 (model_type, namespace_id): {indexdef}"
    )
