"""测试时区归一化 normalize_timezone + ProbeResult."""
from app.engine.drivers._timezone import normalize_timezone
from app.engine.drivers.base import ProbeResult


def test_iana_passthrough():
    assert normalize_timezone("Asia/Shanghai") == "Asia/Shanghai"


def test_offset_to_iana():
    assert normalize_timezone("+08:00") == "Asia/Shanghai"
    assert normalize_timezone("+09:00") == "Asia/Tokyo"
    assert normalize_timezone("-05:00") == "America/New_York"


def test_cst_ambiguous_returns_none():
    assert normalize_timezone("CST") is None  # 中美澳三地歧义


def test_empty_returns_none():
    assert normalize_timezone("") is None
    assert normalize_timezone("SYSTEM") is None


def test_probe_result_dataclass():
    r = ProbeResult(connected=True, detected_timezone="Asia/Shanghai")
    assert r.connected is True
    assert r.failure_reason is None


def test_none_returns_none():
    assert normalize_timezone(None) is None


def test_local_returns_none():
    assert normalize_timezone("LOCAL") is None


def test_unknown_offset_returns_none():
    # 未收录的偏移 -> None, 交前端由用户选 (不猜)
    assert normalize_timezone("+11:00") is None
