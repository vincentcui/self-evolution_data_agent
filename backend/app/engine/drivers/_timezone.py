"""时区探测归一化: 原始探测值 -> IANA 名 or None."""
from zoneinfo import available_timezones

# 常见 UTC 偏移 -> canonical IANA (每偏移一个代表, 勿重复键; 不求全量)
OFFSET_TO_IANA: dict[str, str] = {
    # 东亚 / 南亚 / 大洋洲
    "+08:00": "Asia/Shanghai", "+09:00": "Asia/Tokyo", "+07:00": "Asia/Bangkok",
    "+05:30": "Asia/Kolkata", "+05:45": "Asia/Kathmandu",
    "+10:00": "Australia/Sydney", "+12:00": "Pacific/Auckland",
    # 欧洲 / 中东
    "+00:00": "Europe/London", "+01:00": "Europe/Berlin", "+02:00": "Europe/Athens",
    "+03:00": "Europe/Moscow", "+03:30": "Asia/Tehran",
    # 美洲
    "-03:00": "America/Sao_Paulo", "-03:30": "America/St_Johns",
    "-05:00": "America/New_York", "-06:00": "America/Chicago",
    "-07:00": "America/Denver", "-08:00": "America/Los_Angeles",
    "-09:00": "America/Anchorage", "-10:00": "Pacific/Honolulu",
}
# 歧义缩写 -- 不猜
_AMBIGUOUS = {"CST", "EST", "PST", "MST", "IST"}

# 全量 IANA 名集合 -- 模块级构造一次 (available_timezones() 每调用重建 ~600 项 set), 后续 O(1) 查
_VALID_IANA: frozenset[str] = frozenset(available_timezones())


def normalize_timezone(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    if not raw or raw.upper() in ("SYSTEM", "LOCAL"):
        return None
    if raw in _AMBIGUOUS:
        return None
    if raw in _VALID_IANA:
        return raw
    return OFFSET_TO_IANA.get(raw)
