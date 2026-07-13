"""Stage 1 — 确定性渲染器 render_chart 单测. 纯函数, 真实 DataFrame, 不 mock."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from app.engine.visualizer import render_chart


class TestRenderChartFailSafe:
    def test_empty_rows_falls_back_to_table(self):
        assert render_chart([], {"chart_type": "line", "x": "d", "value": "v"}) == ("table", {})

    def test_illegal_chart_type_falls_back_to_table(self):
        rows = [{"d": "2024-01-01", "v": 10}]
        ct, opt = render_chart(rows, {"chart_type": "xxx", "x": "d", "value": "v"})
        assert ct == "table"

    def test_value_column_not_in_rows_falls_back_to_table(self):
        rows = [{"d": "2024-01-01", "v": 10}]
        ct, opt = render_chart(rows, {"chart_type": "line", "x": "d", "value": "missing"})
        assert ct == "table"

    def test_renderer_never_raises_on_garbage_spec(self):
        rows = [{"d": "2024-01-01", "v": 10}]
        # 缺字段 / None / 错类型都不许抛
        for spec in [{}, {"chart_type": "line"}, {"chart_type": None},
                     {"chart_type": "bar", "x": None, "value": None}]:
            ct, opt = render_chart(rows, spec)
            assert isinstance(ct, str) and isinstance(opt, dict)


class TestRenderLine:
    def test_multi_series_pivot_dedups_x(self):
        # 复刻 D/G 案例结构: 同一 x 多行, 按 series_by 分两条线 (用通用词)
        rows = [
            {"day": "2024-01-01", "region": "north", "amount": 10},
            {"day": "2024-01-01", "region": "south", "amount": 20},
            {"day": "2024-01-02", "region": "north", "amount": 11},
            {"day": "2024-01-02", "region": "south", "amount": 21},
        ]
        ct, opt = render_chart(rows, {
            "chart_type": "line", "x": "day", "series_by": "region", "value": "amount",
        })
        assert ct == "line"
        # x 轴去重: 两天而非四行
        assert opt["xAxis"]["data"] == ["2024-01-01", "2024-01-02"]
        # 两条 series (north/south)
        assert len(opt["series"]) == 2
        names = {s["name"] for s in opt["series"]}
        assert names == {"north", "south"}
        # 每条 series 数据按 x 对齐
        by_name = {s["name"]: s["data"] for s in opt["series"]}
        assert by_name["north"] == [10, 11]
        assert by_name["south"] == [20, 21]

    def test_single_series_no_series_by(self):
        rows = [
            {"day": "2024-01-01", "amount": 10},
            {"day": "2024-01-02", "amount": 11},
        ]
        ct, opt = render_chart(rows, {"chart_type": "line", "x": "day", "value": "amount"})
        assert ct == "line"
        assert len(opt["series"]) == 1
        assert opt["series"][0]["data"] == [10, 11]
        assert opt["xAxis"]["data"] == ["2024-01-01", "2024-01-02"]

    def test_line_x_preserves_upstream_row_order(self):
        # 渲染器不越权排序: 保留 SQL/planner 的 ORDER BY 行序, 原样透传.
        rows = [
            {"day": "2024-01-02", "amount": 11},
            {"day": "2024-01-01", "amount": 10},
        ]
        ct, opt = render_chart(rows, {"chart_type": "line", "x": "day", "value": "amount"})
        assert opt["xAxis"]["data"] == ["2024-01-02", "2024-01-01"]
        # x 与 value 对齐: 行序不乱, 值跟着行走.
        assert opt["series"][0]["data"] == [11, 10]

    def test_bar_humanized_month_not_lexicographic(self):
        # 回归: humanize 后中文月份轴禁字典序 ("10月"<"1月" 会把 10/11/12 顶到最前).
        # 上游按 month 码 1..12 有序; humanize 映射 → 中文标签; x 轴须保持 1→12.
        rows = [{"month": str(m), "spend": m * 100} for m in range(1, 13)]
        code_label_map = {"month": {str(m): f"{m}月" for m in range(1, 13)}}
        ct, opt = render_chart(rows, {
            "chart_type": "bar", "x": "month", "value": "spend",
            "code_label_map": code_label_map,
        })
        assert ct == "bar"
        assert opt["xAxis"]["data"] == [f"{m}月" for m in range(1, 13)]
        assert opt["series"][0]["data"] == [m * 100 for m in range(1, 13)]

    def test_missing_value_in_pivot_cell_is_none(self):
        # north 缺 2024-01-02 → 该格补 None (ECharts 断线)
        rows = [
            {"day": "2024-01-01", "region": "north", "amount": 10},
            {"day": "2024-01-01", "region": "south", "amount": 20},
            {"day": "2024-01-02", "region": "south", "amount": 21},
        ]
        ct, opt = render_chart(rows, {
            "chart_type": "line", "x": "day", "series_by": "region", "value": "amount",
        })
        by_name = {s["name"]: s["data"] for s in opt["series"]}
        assert by_name["north"] == [10, None]
        assert by_name["south"] == [20, 21]

    def test_numeric_overflow_protection(self):
        rows = [
            {"day": "2024-01-01", "amount": 10},
            {"day": "2024-01-02", "amount": float('inf')},
            {"day": "2024-01-03", "amount": float('nan')},
        ]
        ct, opt = render_chart(rows, {"chart_type": "line", "x": "day", "value": "amount"})
        assert ct == "line"
        data = opt["series"][0]["data"]
        assert data == [10, None, None]  # inf/nan → None


class TestRenderBarPieCard:
    def test_bar_dedups_and_aggregates_x(self):
        rows = [{"cat": "a", "n": 3}, {"cat": "b", "n": 5}, {"cat": "c", "n": 7}]
        ct, opt = render_chart(rows, {"chart_type": "bar", "x": "cat", "value": "n"})
        assert ct == "bar"
        assert opt["xAxis"]["data"] == ["a", "b", "c"]
        assert opt["series"][0]["data"] == [3, 5, 7]

    def test_pie_builds_name_value(self):
        rows = [{"cat": "a", "n": 3}, {"cat": "b", "n": 5}]
        ct, opt = render_chart(rows, {"chart_type": "pie", "x": "cat", "value": "n"})
        assert ct == "pie"
        data = opt["series"][0]["data"]
        assert {"name": "a", "value": 3} in data
        assert {"name": "b", "value": 5} in data

    def test_card_single_value(self):
        rows = [{"total": 42}]
        ct, opt = render_chart(rows, {"chart_type": "card", "value": "total"})
        assert ct == "card"
        assert opt.get("value") == 42 or any(c.get("value") == 42 for c in opt.get("cards", []))

    def test_card_honors_value_single_card_among_many_cols(self):
        # 单行多列 + 指定 value → 只出 value 那一张卡 (尊重 chart_spec)
        rows = [{"total_sales": 100, "order_count": 7, "region": "north"}]
        ct, opt = render_chart(rows, {"chart_type": "card", "value": "total_sales"})
        assert ct == "card"
        assert opt.get("value") == 100
        assert opt.get("label") == "total_sales"
        assert "cards" not in opt  # 不再把所有列铺成多卡

    def test_card_empty_value_spreads_all_cols(self):
        # value 留空 (spec 允许) → 单行多列铺多卡 (向后兼容)
        rows = [{"a": 1, "b": 2}]
        ct, opt = render_chart(rows, {"chart_type": "card"})
        assert ct == "card"
        labels = {c["label"] for c in opt.get("cards", [])}
        assert labels == {"a", "b"}

    def test_card_value_not_in_columns_falls_back_to_table(self):
        rows = [{"total": 42}]
        ct, opt = render_chart(rows, {"chart_type": "card", "value": "missing"})
        assert ct == "table"


class TestDatetimeXAndDecimal:
    """回归 trace e1f70ac0: datetime x 列 + Decimal 度量 → series 全 None / 字符串值.

    根因: _render_line/_render_bar 用 df[x].astype(str) (向量化, datetime64→date-only)
    建 x_data, 却用 str(r[x]) (标量 Timestamp→带时间) 建 lookup key, 双路径不一致致
    lookup 全 miss. MySQL SUM 返回 Decimal, _to_serializable 原样返回 → 序列化成字符串,
    ECharts value 轴不可靠.
    """

    def test_line_datetime_x_with_series_by_not_all_none(self):
        # record_date 为 datetime.datetime (MySQL DATETIME 列原样返回)
        rows = [
            {"station_name": "D", "record_date": datetime(2024, 1, 1), "daily_total": 130996},
            {"station_name": "D", "record_date": datetime(2024, 1, 2), "daily_total": 78321},
            {"station_name": "G", "record_date": datetime(2024, 1, 1), "daily_total": 90000},
            {"station_name": "G", "record_date": datetime(2024, 1, 2), "daily_total": 91000},
        ]
        ct, opt = render_chart(rows, {
            "chart_type": "line", "x": "record_date",
            "series_by": "station_name", "value": "daily_total",
        })
        assert ct == "line"
        by_name = {s["name"]: s["data"] for s in opt["series"]}
        # 关键断言: 不许全 None (原 bug 表现)
        assert by_name["D"] == [130996, 78321]
        assert by_name["G"] == [90000, 91000]

    def test_line_datetime_x_single_series_not_all_none(self):
        rows = [
            {"record_date": datetime(2024, 1, 1), "daily_total": 130996},
            {"record_date": datetime(2024, 1, 2), "daily_total": 78321},
        ]
        ct, opt = render_chart(rows, {
            "chart_type": "line", "x": "record_date", "value": "daily_total",
        })
        assert ct == "line"
        assert opt["series"][0]["data"] == [130996, 78321]

    def test_bar_datetime_x_with_series_by_not_all_none(self):
        rows = [
            {"station_name": "D", "record_date": datetime(2024, 1, 1), "daily_total": 130996},
            {"station_name": "G", "record_date": datetime(2024, 1, 1), "daily_total": 90000},
        ]
        ct, opt = render_chart(rows, {
            "chart_type": "bar", "x": "record_date",
            "series_by": "station_name", "value": "daily_total",
        })
        assert ct == "bar"
        by_name = {s["name"]: s["data"] for s in opt["series"]}
        assert by_name["D"] == [130996]
        assert by_name["G"] == [90000]

    def test_date_x_still_works(self):
        # datetime.date 不触发 bug, 须保持不回归
        rows = [
            {"station_name": "D", "day": date(2024, 1, 1), "total": 10},
            {"station_name": "G", "day": date(2024, 1, 1), "total": 20},
        ]
        ct, opt = render_chart(rows, {
            "chart_type": "line", "x": "day", "series_by": "station_name", "value": "total",
        })
        by_name = {s["name"]: s["data"] for s in opt["series"]}
        assert by_name["D"] == [10]
        assert by_name["G"] == [20]

    def test_decimal_value_coerced_to_number(self):
        # MySQL SUM → Decimal; _to_serializable 须强转 number (非字符串)
        rows = [
            {"day": "2024-01-01", "daily_total": Decimal("130996")},
            {"day": "2024-01-02", "daily_total": Decimal("78321")},
        ]
        ct, opt = render_chart(rows, {"chart_type": "line", "x": "day", "value": "daily_total"})
        data = opt["series"][0]["data"]
        assert data == [130996.0, 78321.0]
        assert all(isinstance(v, (int, float)) for v in data)


class TestCodeLabelMap:
    def test_code_label_map_replaces_all_rows(self):
        rows = [{"region": "1", "n": 10}, {"region": "2", "n": 20}, {"region": "1", "n": 5}]
        ct, opt = render_chart(rows, {
            "chart_type": "bar", "x": "region", "value": "n",
            "code_label_map": {"region": {"1": "north", "2": "south"}},
        })
        # 全量替换, 含重复行
        assert set(opt["xAxis"]["data"]) == {"north", "south"}
        assert "1" not in opt["xAxis"]["data"]

    def test_code_label_map_missing_key_keeps_original(self):
        rows = [{"region": "9", "n": 10}]
        ct, opt = render_chart(rows, {
            "chart_type": "bar", "x": "region", "value": "n",
            "code_label_map": {"region": {"1": "north"}},
        })
        assert opt["xAxis"]["data"] == ["9"]
