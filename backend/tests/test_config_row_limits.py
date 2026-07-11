"""
Task 1: 2-number cap validator 测试
验证 default_limit <= hard_ceiling 不变量在 Settings 初始化时强制执行
"""
import pytest
from pydantic import ValidationError
from app.config import Settings


def test_default_limit_le_hard_ceiling_ok():
    s = Settings(default_limit=1000, hard_ceiling=20000)
    assert s.default_limit == 1000
    assert s.hard_ceiling == 20000


def test_default_limit_gt_hard_ceiling_rejected():
    with pytest.raises(ValidationError):
        Settings(default_limit=20000, hard_ceiling=1000)


def test_defaults_set():
    s = Settings()
    assert s.default_limit == 1000
    assert s.hard_ceiling == 20000
