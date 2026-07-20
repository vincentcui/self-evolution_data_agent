"""
_discover_language_configs 容错测试.

覆盖: 某语言 grammar 不可用 (DownloadError) 时跳过该语言, 其他语言保留, 不崩模块.
这是单语言 grammar 缺失不该击穿整个 skeleton 模块的防御性契约.
"""
from unittest.mock import patch

from tree_sitter_language_pack import DownloadError

from app.knowledge.skeleton._base import _discover_language_configs


def test_download_error_skips_language_not_crash():
    """某语言 grammar DownloadError → 跳过该语言, 其他语言保留, 不抛异常."""

    def fake_get_parser(lang):
        if lang == "c_sharp":
            raise DownloadError("mock: c_sharp not available for download")
        return object()  # 其他语言返回假 parser (通过验证)

    with patch("tree_sitter_language_pack.get_parser", side_effect=fake_get_parser):
        configs = _discover_language_configs()

    assert "csharp" not in configs      # c_sharp 下载失败 → 跳过
    assert "java" in configs             # 其他语言保留
    assert "python" in configs


def test_all_languages_load_when_grammar_available():
    """所有 grammar 可用时正常加载 (不跳过, 数量符合预期)."""
    with patch("tree_sitter_language_pack.get_parser", return_value=object()):
        configs = _discover_language_configs()

    # languages/ 目录 13 个语言 config, 全部 grammar 可用 → 全加载
    assert len(configs) >= 10
    assert "csharp" in configs


def test_real_csharp_skip_when_not_downloaded():
    """真实环境: c_sharp 默认未下载 (tree_sitter_language_pack 1.9+ 不预装) → csharp 跳过.

    不 mock, 走真实 get_parser. 若环境已预装 c_sharp (download_all 过) 则 csharp 在内,
    该测试验证的是"容错路径不崩", 而非强依赖 c_sharp 必须缺失.
    """
    configs = _discover_language_configs()  # 不应抛任何异常
    # 无论 c_sharp 是否可用, 模块都不该崩, 且主流语言必在
    assert "java" in configs
    assert "python" in configs
