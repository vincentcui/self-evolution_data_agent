"""DashScopeEmbeddingFunction 从 registry 取 client."""
from unittest.mock import PropertyMock, patch

import pytest

from app.engine.model_registry import ModelRegistry, registry


def test_embedding_init_uses_registry():
    """DashScopeEmbeddingFunction 用 registry.get_embedding_client() 构造."""
    from app.engine.embedding import DashScopeEmbeddingFunction
    with patch.object(registry, 'get_embedding_client') as mock_get:
        mock_get.return_value = object()
        with patch.object(ModelRegistry, 'embedding_config', new_callable=PropertyMock) as mock_cfg:
            mock_cfg.return_value = {'model_name': 'test', 'base_url': 'https://x/v1'}
            fn = DashScopeEmbeddingFunction()
            mock_get.assert_called_once()
            assert fn._client is mock_get.return_value


def test_embedding_init_raises_when_no_config():
    """__init__ 无激活配置时抛 RuntimeError."""
    from app.engine.embedding import DashScopeEmbeddingFunction
    with patch.object(registry, 'get_embedding_client', side_effect=RuntimeError("无激活")):
        with pytest.raises(RuntimeError, match="无激活"):
            DashScopeEmbeddingFunction()


def test_build_from_config_uses_registry():
    """ChromaDB 0.5+ 持久化重载走 build_from_config → __init__ → registry."""
    from app.engine.embedding import DashScopeEmbeddingFunction
    with patch.object(registry, 'get_embedding_client') as mock_get:
        mock_get.return_value = object()
        with patch.object(ModelRegistry, 'embedding_config', new_callable=PropertyMock) as mock_cfg:
            mock_cfg.return_value = {'model_name': 'test', 'base_url': 'https://x/v1'}
            fn = DashScopeEmbeddingFunction.build_from_config({})
            mock_get.assert_called_once()
            assert fn._client is mock_get.return_value
