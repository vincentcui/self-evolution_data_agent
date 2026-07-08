import pytest

from app.models.model_config import DEFAULT_MAX_HISTORY_TURNS, ModelConfig


@pytest.mark.asyncio
async def test_new_chat_config_defaults_to_constant(db):
    """新建 CHAT 行 max_history_turns 默认 = DEFAULT_MAX_HISTORY_TURNS."""
    row = ModelConfig(
        provider="openai", base_url="http://x", api_key="k",
        model_name="gpt-4o", model_type="CHAT", is_active=False,
    )
    db.add(row)
    await db.flush()
    assert row.max_history_turns == DEFAULT_MAX_HISTORY_TURNS


@pytest.mark.asyncio
async def test_max_history_turns_settable(db):
    row = ModelConfig(
        provider="openai", base_url="http://x", api_key="k",
        model_name="gpt-4o", model_type="CHAT", is_active=False,
        max_history_turns=10,
    )
    db.add(row)
    await db.flush()
    assert row.max_history_turns == 10


def test_row_to_dict_transports_max_history_turns():
    from app.engine.model_registry import ModelRegistry
    reg = ModelRegistry()
    row = ModelConfig(
        provider="openai", base_url="http://x", api_key="k",
        model_name="gpt-4o", model_type="CHAT", is_active=False,
        max_history_turns=8,
    )
    cfg = reg._row_to_dict(row)
    assert cfg["max_history_turns"] == 8


def test_default_constant_value():
    """默认常量锁定为 5(与 DDL / 前端跨语言一致的语义锚点)."""
    assert DEFAULT_MAX_HISTORY_TURNS == 5
