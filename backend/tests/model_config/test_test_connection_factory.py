"""test_connection 端点必须走 llm 工厂, 不得直接构造 client."""
from unittest.mock import patch

from app.api.model_config import _test_anthropic_chat, _test_openai_chat, _test_openai_embedding


def test_test_openai_chat_uses_factory():
    """_test_openai_chat 内部 lazy import 从 app.engine.llm 解析 build_openai_client."""
    with patch("app.engine.llm.build_openai_client") as mock_fac:
        mock_fac.return_value.chat.completions.create.return_value = object()
        _test_openai_chat({"api_key": "k", "base_url": "https://x/v1", "model_name": "m"})
        mock_fac.assert_called_once()


def test_test_openai_chat_wires_proxy_url():
    with patch("app.engine.llm.build_openai_client") as mock_fac:
        mock_fac.return_value.chat.completions.create.return_value = object()
        _test_openai_chat({"api_key": "k", "base_url": "https://x/v1",
                           "model_name": "m", "proxy_url": "http://h:8080"})
        assert mock_fac.call_args.kwargs.get("proxy_url") == "http://h:8080"


def test_test_anthropic_chat_uses_factory():
    with patch("app.engine.llm.build_anthropic_client") as mock_fac:
        mock_fac.return_value.messages.create.return_value = object()
        _test_anthropic_chat({"api_key": "k", "base_url": "https://x/v1", "model_name": "m"})
        mock_fac.assert_called_once()


def test_test_openai_embedding_uses_factory():
    with patch("app.engine.llm.build_openai_client") as mock_fac:
        mock_fac.return_value.embeddings.create.return_value = object()
        _test_openai_embedding({"api_key": "k", "base_url": "https://x/v1", "model_name": "m"})
        mock_fac.assert_called_once()
