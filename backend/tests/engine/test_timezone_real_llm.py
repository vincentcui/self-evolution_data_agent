"""C2 验收门: 真实 LLM 自算时区边界. 接真实 LLM 不 mock. 夏令时算错→复议 C3."""
from __future__ import annotations

import asyncio
import os
import re

import pytest

from app.engine.llm import chat_completion

# 触发门: 任意非空值表示 "我愿跑真实 LLM live 测试". 真实凭据不走 env, 走元数据库
# model_configs 表激活的 CHAT 配置 (见 registry.load_from_db); env var 仅作 pytest skipif 信号.
_OPENAI_AVAILABLE = bool(os.environ.get("IS_LLM_API_KEY"))

# 从 registry.py SYSTEM_PROMPT 步骤 8 + fetch_schema description 的时区引导提取
_TIMEZONE_GUIDE = (
    "查日期范围 (如'X年X月') 时按此时区算 [月初00:00, 下月初00:00) 边界, "
    "生成带时区偏移的日期字面量 (document 系用 Extended JSON "
    '{"$date":"2026-06-01T00:00:00+08:00"}, '
    "relational 系用 SQL 日期字面量), 勿用 UTC 零点."
)

# 匹配 ISO 8601 日期 + 时区偏移: 2026-03-01T00:00:00+08:00
_DATETIME_OFFSET_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}"
)


@pytest.fixture(autouse=True)
def _load_active_chat_config() -> None:
    """live 测试前从元数据库加载激活的 CHAT 配置到 registry.

    registry.load_from_db() 是应用启动钩子 (async), pytest 不跑; 不显式加载则
    chat_completion 取到 chat_config=None 抛 RuntimeError. 凭据源是 model_configs
    表 (非 env), 故此处只需 load, 不需传 key. sync test 无法直接用 async fixture,
    故 asyncio.run 包一层.
    """
    from app.engine.model_registry import registry

    asyncio.run(registry.load_from_db())
    if not registry.is_ready()["chat_ready"]:
        pytest.skip("元数据库无激活 CHAT 配置 (model_configs.is_active=True 缺失)")


@pytest.mark.live
@pytest.mark.skipif(not _OPENAI_AVAILABLE, reason="需真实 LLM (IS_LLM_API_KEY)")
def test_llm_compute_date_range_shanghai() -> None:
    """timezone=Asia/Shanghai, 问 2026 年 6 月.
    验证 LLM 产出边界带 +08:00 偏移.
    """
    system_prompt = (
        "你是数据分析助手。数据源时区为 Asia/Shanghai (UTC+8, 无夏令时)。\n"
        + _TIMEZONE_GUIDE
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "统计2026年6月创建的orders"},
    ]
    text = chat_completion(messages=messages)
    assert text, "LLM response 不应为空"

    # 验证响应含 +08:00 偏移 (Shanghai 全年 UTC+8)
    assert "+08:00" in text, (
        f"应含 Shanghai 时区偏移 +08:00, 实际响应:\n{text}"
    )
    # 验证 June/July 日期至少出现一个月
    date_hits = [m for m in _DATETIME_OFFSET_RE.findall(text) if m.endswith("+08:00")]
    assert date_hits, (
        f"响应中未找到 +08:00 偏移的 ISO 日期, 实际响应:\n{text}"
    )


@pytest.mark.live
@pytest.mark.skipif(not _OPENAI_AVAILABLE, reason="需真实 LLM (IS_LLM_API_KEY)")
def test_llm_compute_date_range_los_angeles_dst() -> None:
    """timezone=America/Los_Angeles, 问 2026 年 3 月 (3/8 PST→PDT).
    验证 LLM 正确处理夏令时: 3 月 1 日 PST(-08:00), 4 月 1 日 PDT(-07:00).
    算错→test failed→触发复议 C3 (加 compute_date_range 工具).
    """
    system_prompt = (
        "你是数据分析助手。数据源时区为 America/Los_Angeles (有夏令时 PST/PDT)。\n"
        + _TIMEZONE_GUIDE
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "统计2026年3月创建的orders"},
    ]
    text = chat_completion(messages=messages)
    assert text, "LLM response 不应为空"

    # 提取所有带偏移的 ISO 8601 日期时间字符串
    matches = _DATETIME_OFFSET_RE.findall(text)

    # C2 验收门: LLM 必须同时输出 PST(-08:00) 和 PDT(-07:00) 偏移.
    # 若只有一种偏移 → 未正确处理 DST → test failed → 复议 C3.
    has_pst = any(m.endswith("-08:00") for m in matches)
    has_pdt = any(m.endswith("-07:00") for m in matches)

    assert has_pst and has_pdt, (
        f"LLM 未正确处理 DST: 应同时含 -08:00 (PST) 和 -07:00 (PDT) 偏移. "
        f"匹配到的日期: {matches}\n全文:\n{text}"
    )
