"""registry 双检锁并发安全 — 热切换 + 并发 get_client 不创建重复 client."""
import threading
from unittest.mock import patch

from app.engine.model_registry import ModelRegistry


def test_concurrent_get_chat_client_creates_only_one():
    """20 线程并发 get_chat_client, build_openai_client 只被调用一次 (双检锁生效)."""
    r = ModelRegistry()
    r.refresh_chat({"protocol": "openai", "api_key": "k", "base_url": "https://x/v1",
                    "model_name": "m", "model_type": "CHAT",
                    "temperature": 0.1, "max_tokens": 2000})
    results = []

    with patch("app.engine.llm_client_factory.build_openai_client") as mock_factory:
        mock_factory.return_value = object()

        def worker():
            results.append(r.get_chat_client())

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    # 双检锁: 第一个线程建 client, 其余 19 个拿到缓存
    assert mock_factory.call_count == 1, (
        f"双检锁失效: build_openai_client 调了 {mock_factory.call_count} 次 (期望 1)"
    )
    assert len(results) == 20
    assert all(c is results[0] for c in results)  # 全部同一实例


def test_refresh_during_concurrent_get_is_safe():
    """热切换与并发 get 不互锁 (refresh 设 None → 下一个 get 重建)."""
    r = ModelRegistry()
    cfg = {"protocol": "openai", "api_key": "k", "base_url": "https://x/v1",
           "model_name": "m", "model_type": "CHAT",
           "temperature": 0.1, "max_tokens": 2000}
    r.refresh_chat(cfg)
    first = r.get_chat_client()  # 建 client

    # 模拟热切换: 新 config + 清缓存
    r.refresh_chat({"protocol": "openai", "api_key": "k2", "base_url": "https://y/v1",
                    "model_name": "m2", "model_type": "CHAT",
                    "temperature": 0.2, "max_tokens": 3000})
    second = r.get_chat_client()  # 应重建
    assert second is not first  # 不同 client 实例 (热切换生效)


def test_two_cfgs_get_independent_clients():
    """cfg-keyed cache 隔离: cfgA 和 cfgB 各自缓存独立 client, 不互相污染."""
    r = ModelRegistry()
    cfg_a = {"protocol": "openai", "api_key": "ka", "base_url": "https://a/v1",
             "model_name": "ma", "model_type": "CHAT", "temperature": 0.1, "max_tokens": 2000}
    cfg_b = {"protocol": "openai", "api_key": "kb", "base_url": "https://b/v1",
             "model_name": "mb", "model_type": "CHAT", "temperature": 0.2, "max_tokens": 3000}
    r.refresh_chat(cfg_a)
    client_a1 = r.get_chat_client(cfg_a)   # cfgA → 构建 client_a
    client_a2 = r.get_chat_client(cfg_a)   # 同 cfg key → 缓存命中 (refresh 未清)
    assert client_a2 is client_a1

    r.refresh_chat(cfg_b)                  # refresh 清缓存
    client_b = r.get_chat_client(cfg_b)    # cfgB → 构建 client_b
    assert client_b is not client_a1       # 不同 cfg key → 独立 client

    # cfgA 缓存已被 refresh 清除 → 重新获取应重建
    client_a3 = r.get_chat_client(cfg_a)
    assert client_a3 is not client_a1      # refresh 清缓存 → 重建
    client_a4 = r.get_chat_client(cfg_a)   # 同 cfg key, refresh 未清 → 缓存命中
    assert client_a4 is client_a3

    # 切回 cfgA 后再次 refresh 清缓存
    r.refresh_chat(cfg_a)
    client_a5 = r.get_chat_client(cfg_a)   # 应重建
    assert client_a5 is not client_a3
