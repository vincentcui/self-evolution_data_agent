"""registry 公开接口单测 — chat_config/embedding_config/get_chat_client/get_embedding_client."""
import pytest

from app.engine.model_registry import ModelRegistry


def test_chat_config_returns_none_when_no_active():
    r = ModelRegistry()
    assert r.chat_config is None


def test_embedding_config_returns_none_when_no_active():
    r = ModelRegistry()
    assert r.embedding_config is None


def test_get_chat_client_raises_when_no_active():
    r = ModelRegistry()
    with pytest.raises(RuntimeError, match="无激活的 Chat"):
        r.get_chat_client()


def test_get_embedding_client_raises_when_no_active():
    r = ModelRegistry()
    with pytest.raises(RuntimeError, match="无激活的 Embedding"):
        r.get_embedding_client()


def test_chat_config_returns_dict_after_refresh():
    r = ModelRegistry()
    cfg = {"protocol": "openai", "api_key": "k", "base_url": "https://x/v1",
           "model_name": "m", "model_type": "CHAT", "temperature": 0.1, "max_tokens": 2000}
    r.refresh_chat(cfg)
    assert r.chat_config is not None
    assert r.chat_config["model_name"] == "m"


def test_get_chat_client_returns_client_after_refresh():
    r = ModelRegistry()
    r.refresh_chat({"protocol": "openai", "api_key": "k", "base_url": "https://x/v1",
                    "model_name": "m", "model_type": "CHAT", "temperature": 0.1,
                    "max_tokens": 2000})
    client = r.get_chat_client()
    assert client is not None  # OpenAI 实例


def test_refresh_none_clears_chat_config():
    """热切换删除配置: refresh_chat(None) → chat_config 返回 None."""
    r = ModelRegistry()
    r.refresh_chat({"protocol": "openai", "api_key": "k", "base_url": "https://x/v1",
                    "model_name": "m", "model_type": "CHAT", "temperature": 0.1,
                    "max_tokens": 2000})
    assert r.chat_config is not None
    r.refresh_chat(None)
    assert r.chat_config is None


def test_get_chat_client_raises_after_refresh_none():
    """refresh_chat(None) 后 get_chat_client() → RuntimeError (G8 路径)."""
    r = ModelRegistry()
    cfg = {"protocol": "openai", "api_key": "k", "base_url": "https://x/v1",
           "model_name": "m", "model_type": "CHAT", "temperature": 0.1, "max_tokens": 2000}
    r.refresh_chat(cfg)
    r.get_chat_client()  # 造 client
    r.refresh_chat(None)  # 删配置
    with pytest.raises(RuntimeError, match="无激活的 Chat"):
        r.get_chat_client()
