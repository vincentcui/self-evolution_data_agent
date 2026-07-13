"""
可视化智能推荐 — 确定性渲染器
图表类型/列角色由 LLM 在 present_result 的 chart_spec 给出; 渲染器只机械拼 option.
(旧 dtype 启发式 recommend_chart 已于 Stage 2 删除, 反转为 render_chart.)
"""

import math
from decimal import Decimal

import numpy as np
import pandas as pd

# ════════════════════════════════════════════
#  内部工具函数
# ════════════════════════════════════════════

def _to_serializable(val):
    """单值 JSON 化: pd.isna / inf / -inf → None, Decimal → float, 其余数值原样.

    ECharts value 轴需 number; MySQL 聚合 (SUM 等) 返回 Decimal, 若不强转会被
    上层 json 序列化成字符串, 致数值轴渲染不可靠.
    """
    if val is None:
        return None
    if isinstance(val, float):
        # 纯 Python float 在此分支内闭合处理: inf/nan → None, 否则原样 (本身已 JSON 可序列化).
        return None if (pd.isna(val) or math.isinf(val)) else val
    if isinstance(val, Decimal):
        # MySQL SUM/AVG 返回 Decimal; 强转 float 供 ECharts value 轴直绘.
        fv = float(val)
        return None if (math.isnan(fv) or math.isinf(fv)) else fv
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(val, (np.integer,)):
        return int(val)
    if isinstance(val, (np.floating,)):
        fv = float(val)
        return None if math.isinf(fv) or math.isnan(fv) else fv
    if hasattr(val, "item"):
        return val.item()
    return val


# ════════════════════════════════════════════
#  Stage 1: 确定性渲染器 (LLM 出列角色, 此处机械渲染)
#  反转自旧 recommend_chart: 不再 dtype 猜类型, 只按 chart_spec 拼 option.
# ════════════════════════════════════════════

_VALID_CHART_TYPES = frozenset({"card", "line", "pie", "bar", "table"})


def render_chart(rows: list[dict], chart_spec: dict) -> tuple[str, dict]:
    """确定性渲染. fail-safe: 任何异常/列对不上 → ("table", {})."""
    try:
        return _render_chart_impl(rows, chart_spec)
    except Exception:  # noqa: BLE001 — 渲染器永不抛, 列对不上落表格
        return "table", {}


def _render_chart_impl(rows: list[dict], chart_spec: dict) -> tuple[str, dict]:
    if not rows:
        return "table", {}
    chart_type = (chart_spec or {}).get("chart_type")
    if chart_type not in _VALID_CHART_TYPES:
        return "table", {}
    if chart_type == "table":
        return "table", {}
    # 应用兜底 humanize 映射到全量数据
    rows = _apply_code_label_map(rows, (chart_spec or {}).get("code_label_map") or {})
    df = pd.DataFrame(rows)
    if chart_type == "card":
        return _render_card(df, (chart_spec or {}).get("value") or "")
    x = chart_spec.get("x") or ""
    value = chart_spec.get("value") or ""
    series_by = chart_spec.get("series_by") or ""
    # 列存在性校验 — 对不上 fail-safe
    if value and value not in df.columns:
        return "table", {}
    if x and x not in df.columns:
        return "table", {}
    if series_by and series_by not in df.columns:
        return "table", {}
    if chart_type == "line":
        return _render_line(df, x, value, series_by)
    if chart_type == "pie":
        return _render_pie(df, x, value)
    if chart_type == "bar":
        return _render_bar(df, x, value, series_by)
    return "table", {}


def _apply_code_label_map(rows: list[dict], code_label_map: dict) -> list[dict]:
    """对全量 rows 应用 {列名: {code: label}} 替换. code→label 兜底 humanize."""
    if not code_label_map:
        return rows
    out = []
    for r in rows:
        nr = dict(r)
        for col, mapping in code_label_map.items():
            if col in nr:
                key = str(nr[col])
                if key in mapping:
                    nr[col] = mapping[key]
        out.append(nr)
    return out


def _render_axis_chart(
    df: pd.DataFrame, x: str, value: str, series_by: str, chart_type: str
) -> tuple[str, dict]:
    """line / bar 共享渲染. x 按上游行序去重; series_by 非空时按其唯一值 pivot 出多条.

    x 列只字符串化一次 (向量化 ``df[x].astype(str)``), x_data 与 lookup key 同源复用 ——
    标量 ``str(r[x])`` 与向量化 ``astype(str)`` 对 datetime64 列产出不同字符串 (date-only
    vs 带时间), 双路径会致 lookup 全 miss → series 全 None (trace e1f70ac0 回归).
    """
    if not x or not value:
        return "table", {}
    # 单一字符串化源: x_data 与 lookup key 共用, 杜绝双路径漂移.
    xs = df[x].astype(str)
    # 保留上游行序 (SQL/planner 的 ORDER BY 意图) — pandas unique 保序去重.
    # 禁用 sorted(): 对 humanize 后的分类轴 ("1月".."12月") 或数字字符串 ("2"/"10")
    # 做字典序会摧毁时间序/排名意图 ("10月"<"1月", "10"<"2").
    x_data = xs.unique().tolist()
    if series_by:
        sb = df[series_by].astype(str)
        series = []
        for key in sb.unique().tolist():
            mask = sb == key
            lookup = dict(
                zip(xs[mask].tolist(), [_to_serializable(v) for v in df.loc[mask, value].tolist()])
            )
            data = [lookup.get(xv) for xv in x_data]
            series.append({"name": key, "type": chart_type, "data": data})
    else:
        lookup = dict(zip(xs.tolist(), [_to_serializable(v) for v in df[value].tolist()]))
        series = [{"name": value, "type": chart_type, "data": [lookup.get(xv) for xv in x_data]}]
    return chart_type, {
        "xAxis": {"type": "category", "data": x_data},
        "yAxis": {"type": "value"},
        "series": series,
        "tooltip": {"trigger": "axis"},
        "legend": {"data": [s["name"] for s in series]} if len(series) > 1 else {},
    }


def _render_line(df: pd.DataFrame, x: str, value: str, series_by: str) -> tuple[str, dict]:
    return _render_axis_chart(df, x, value, series_by, "line")


def _render_bar(df: pd.DataFrame, x: str, value: str, series_by: str) -> tuple[str, dict]:
    return _render_axis_chart(df, x, value, series_by, "bar")


def _render_pie(df: pd.DataFrame, x: str, value: str) -> tuple[str, dict]:
    if not x or not value:
        return "table", {}
    data = [
        {"name": str(r[x]), "value": _to_serializable(r[value])}
        for _, r in df.iterrows()
    ]
    return "pie", {
        "series": [{"type": "pie", "data": data, "radius": "60%"}],
        "tooltip": {"trigger": "item"},
    }


def _render_card(df: pd.DataFrame, value: str = "") -> tuple[str, dict]:
    # 尊重 chart_spec: LLM 指定了 value 就只出该列单卡 (忠实契约).
    if value:
        if value not in df.columns or len(df) != 1:
            return "table", {}  # 指定列不存在 / 非单行 → fail-safe
        return "card", {"value": _to_serializable(df.iloc[0][value]), "label": value}
    # value 留空 (spec 允许): 单值→单卡; 单行多列→多卡 (方向上更完整).
    if df.shape == (1, 1):
        return "card", {"value": _to_serializable(df.iloc[0, 0]), "label": df.columns[0]}
    if len(df) == 1:
        cards = [{"label": c, "value": _to_serializable(df.iloc[0][c])} for c in df.columns]
        return "card", {"cards": cards}
    return "table", {}
