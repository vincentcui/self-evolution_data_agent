/* ════════════════════════════════════════════
 *  ECharts 图表渲染器
 *  支持 line / bar / pie / card / table
 *  当 chart_option 为空时, 根据 rows + columns + chartType 自动构建 ECharts option
 * ════════════════════════════════════════════ */

import React from "react";
import ReactECharts from "echarts-for-react";
import { Card, Statistic, Row, Col } from "antd";
import type { QueryResponse } from "@/types";
import { normalizeRows } from "@/utils/normalizeRows";
import DataTable from "./DataTable";

interface Props {
  result: QueryResponse;
  chartType?: string;
}

/**
 * 从 rows (二维数组) + columns 自动构建 ECharts option
 * 规则:
 *   - categoryColumn 指定的列作为 category (x 轴 / 饼图 name)
 *   - valueColumn 指定的列作为度量 (优先复用 LLM 在 chart_spec 选定的 value)
 *   - 两者未指定时 fallback 到启发式: 第一个非数值列为 category, 其余数值列为 series
 *   - id 类数值列 (user_id / _id 等) 仅作启发式 fallback 时排除, valueColumn 显式给定则尊重
 */
function buildAutoOption(
  type: string,
  rows: any[][],
  columns: string[],
  categoryColumn?: string,
  valueColumn?: string,
  seriesBy?: string,
): Record<string, any> {
  if (!rows.length || !columns.length) return {};

  // 确定 category 列索引
  let catIdx = 0;
  if (categoryColumn) {
    const idx = columns.indexOf(categoryColumn);
    if (idx >= 0) catIdx = idx;
  } else {
    // fallback: 找第一个非数值列
    const nonNumIdx = columns.findIndex((_, i) => {
      const sample = rows.find((r) => r[i] != null)?.[i];
      return typeof sample !== "number" && isNaN(Number(sample));
    });
    if (nonNumIdx >= 0) catIdx = nonNumIdx;
  }

  const categories = rows.map((r) => String(r[catIdx] ?? ""));

  // 标识符列 (user_id / _id / doc_id …) — 数值型但语义是维度不是度量
  const isIdColumn = (name: string) =>
    /(^|_)(id|uid|uuid)(_|\b|$)|^_id$/i.test(name);

  // 找数值列 (排除 category 列)
  const numericCols: number[] = [];
  for (let i = 0; i < columns.length; i++) {
    if (i === catIdx) continue;
    const sample = rows.find((r) => r[i] != null)?.[i];
    if (typeof sample === "number" || !isNaN(Number(sample))) {
      numericCols.push(i);
    }
  }
  if (numericCols.length === 0 && columns.length >= 2) {
    const fallback = catIdx === 0 ? 1 : 0;
    numericCols.push(fallback);
  }

  // value 列: 优先 LLM 选定的 valueColumn, 否则启发式 (排除 id 类列)
  let valueIdx = -1;
  if (valueColumn) {
    const idx = columns.indexOf(valueColumn);
    if (idx >= 0) valueIdx = idx;
  }
  if (valueIdx < 0) {
    const valueKeywords = ["count", "sum", "total", "amount"];
    const candidates = numericCols.filter((i) => !isIdColumn(columns[i]));
    const pool = candidates.length > 0 ? candidates : numericCols;
    valueIdx = pool[0] ?? (catIdx === 0 ? 1 : 0);
    if (pool.length > 1) {
      const preferred = pool.find((i) =>
        valueKeywords.some((kw) => columns[i].toLowerCase().includes(kw)),
      );
      if (preferred !== undefined) valueIdx = preferred;
    }
  }

  if (type === "pie") {
    const data = rows.map((r) => ({
      name: String(r[catIdx] ?? ""),
      value: Number(r[valueIdx] ?? 0),
    }));
    return {
      tooltip: { trigger: "item", formatter: "{b}: {c} ({d}%)" },
      legend: { orient: "vertical", left: "left", top: "middle" },
      series: [
        {
          type: "pie",
          radius: ["40%", "70%"],
          data,
          emphasis: { itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: "rgba(0,0,0,0.5)" } },
        },
      ],
    };
  }

  // line / bar: 有显式 valueColumn 时只画该列, 避免标识符列污染 y 轴尺度;
  // 否则保持原行为 (所有数值列各一条 series) 兼容多 series 场景
  // seriesBy 给定 (LLM chart_spec 透传): 按其唯一值 pivot 出多 series, 与后端 _render_axis_chart 对齐
  if ((type === "line" || type === "bar") && seriesBy && valueIdx >= 0) {
    const sIdx = columns.indexOf(seriesBy);
    if (sIdx >= 0 && sIdx !== catIdx && sIdx !== valueIdx) {
      const categories = Array.from(new Set(rows.map((r) => String(r[catIdx] ?? ""))));
      const seriesKeys = Array.from(new Set(rows.map((r) => String(r[sIdx] ?? ""))));
      const series = seriesKeys.map((k) => ({
        name: k,
        type,
        data: categories.map((c) => {
          const row = rows.find(
            (r) => String(r[catIdx] ?? "") === c && String(r[sIdx] ?? "") === k,
          );
          return row ? Number(row[valueIdx] ?? 0) : null; // 缺格补 null (ECharts 断线/空柱)
        }),
      }));
      return {
        tooltip: { trigger: "axis" },
        legend: { data: seriesKeys },
        xAxis: {
          type: "category",
          data: categories,
          axisLabel: { rotate: categories.length > 8 ? 30 : 0 },
        },
        yAxis: { type: "value" },
        series,
      };
    }
  }

  const seriesCols = valueColumn && valueIdx >= 0 ? [valueIdx] : numericCols;
  const series = seriesCols.map((colIdx) => ({
    name: columns[colIdx],
    type,
    data: rows.map((r) => Number(r[colIdx] ?? 0)),
  }));

  return {
    tooltip: { trigger: "axis" },
    legend: { data: seriesCols.map((i) => columns[i]) },
    xAxis: {
      type: "category",
      data: categories,
      axisLabel: { rotate: categories.length > 8 ? 30 : 0 },
    },
    yAxis: { type: "value" },
    series,
  };
}

export { buildAutoOption };

const ChartRenderer: React.FC<Props> = ({ result, chartType }) => {
  const type = chartType || result.chart_type;
  const option = result.chart_option;
  const rows = normalizeRows(result.rows, result.columns) as any[][];

  /* ── 数字卡片 ── */
  if (type === "card") {
    if (option.cards) {
      return (
        <Row gutter={16}>
          {(option.cards as { label: string; value: any }[]).map((c, i) => (
            <Col key={i} span={6}>
              <Card>
                <Statistic title={c.label} value={c.value} />
              </Card>
            </Col>
          ))}
        </Row>
      );
    }
    return (
      <Card>
        <Statistic title={option.label as string} value={option.value as any} />
      </Card>
    );
  }

  /* ── 表格 ── */
  if (type === "table") {
    return <DataTable columns={result.columns} rows={rows} />;
  }

  /* ── ECharts 图表: 优先用后端 option, 为空时自动构建 ── */
  const userSwitched = chartType && chartType !== result.chart_type;
  const hasOption = !userSwitched && option && Object.keys(option).length > 0;
  const finalOption = hasOption
    ? option
    : buildAutoOption(type, rows, result.columns, result.category_column, result.value_column, result.series_by);

  if (!finalOption || Object.keys(finalOption).length === 0) {
    return <DataTable columns={result.columns} rows={rows} />;
  }

  return <ReactECharts option={finalOption} notMerge={true} style={{ height: 400 }} />;
};

export default ChartRenderer;
