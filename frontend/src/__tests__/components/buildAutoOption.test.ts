/* ════════════════════════════════════════════
 *  buildAutoOption — 切换图表类型时前端自动构建 ECharts option
 *  验证: valueColumn 透传后 line/pie 选对度量列, 不被 user_id 污染
 * ════════════════════════════════════════════ */

import { describe, it, expect } from "vitest";
import { buildAutoOption } from "@/components/ChartRenderer";

// 复刻 trace 3f2c0d6c 现场结构: user_id (数值标识符) + 用户名 (维度) + 订单数 (度量)
const columns = ["user_id", "用户名", "订单数"];
const rows: any[][] = [
  [2302619, "张三", 90],
  [2300334, "李四", 27],
  [2302661, "王五", 26],
  [2302638, "赵六", 24],
];

describe("buildAutoOption — valueColumn 透传修复", () => {
  it("pie: valueColumn=订单数 → 色块值取订单数, 非 user_id", () => {
    const opt = buildAutoOption("pie", rows, columns, "用户名", "订单数");
    const data = (opt.series[0] as any).data as { name: string; value: number }[];
    const values = data.map((d) => d.value);
    expect(values).toEqual([90, 27, 26, 24]);
    // 回归锚点: 绝不是 user_id (否则值会在 230 万级且几乎相等 → 等分饼)
    expect(values.every((v) => v < 1000)).toBe(true);
  });

  it("line: valueColumn=订单数 → 只画一条 series, y 轴不被 user_id 压扁", () => {
    const opt = buildAutoOption("line", rows, columns, "用户名", "订单数");
    const series = opt.series as any[];
    expect(series).toHaveLength(1);
    expect(series[0].name).toBe("订单数");
    expect(series[0].data).toEqual([90, 27, 26, 24]);
  });

  it("bar: valueColumn 给定 → 单 series 用该列", () => {
    const opt = buildAutoOption("bar", rows, columns, "用户名", "订单数");
    const series = opt.series as any[];
    expect(series).toHaveLength(1);
    expect(series[0].data).toEqual([90, 27, 26, 24]);
  });
});

describe("buildAutoOption — 启发式 fallback (无 valueColumn)", () => {
  it("pie: 无 valueColumn 时排除 id 类列, 不误选 user_id", () => {
    const opt = buildAutoOption("pie", rows, columns, "用户名");
    const data = (opt.series[0] as any).data as { name: string; value: number }[];
    const values = data.map((d) => d.value);
    expect(values.every((v) => v < 1000)).toBe(true);
  });

  it("line: 无 valueColumn 时保持原行为 (所有数值列各一条 series)", () => {
    const opt = buildAutoOption("line", rows, columns, "用户名");
    const series = opt.series as any[];
    expect(series.length).toBeGreaterThan(1);
  });
});

/* ════════════════════════════════════════════
 *  seriesBy pivot — 切换图表类型时复用 LLM 选定的分组列
 *  回归 trace e1f70ac0: 后端折线空白后用户切柱状图, 旧 buildAutoOption
 *  无 series_by → 仅 daily_total 单 series, 丢失站点分组.
 * ════════════════════════════════════════════ */
describe("buildAutoOption — seriesBy multi-series pivot", () => {
  // 通用域: 两站点 × 两日 的日度量 (镜像 D/G × record_date × daily_total 结构)
  const cols = ["station", "record_date", "daily_total"];
  const srows: any[][] = [
    ["D", "2024-01-01", 130996],
    ["D", "2024-01-02", 78321],
    ["G", "2024-01-01", 90000],
    ["G", "2024-01-02", 91000],
  ];

  it("bar + seriesBy=station → 两条 series (D/G), 按 record_date 对齐", () => {
    const opt = buildAutoOption("bar", srows, cols, "record_date", "daily_total", "station");
    const series = opt.series as any[];
    expect(series).toHaveLength(2);
    const byName = Object.fromEntries(series.map((s) => [s.name, s.data])) as Record<string, any[]>;
    expect(byName["D"]).toEqual([130996, 78321]);
    expect(byName["G"]).toEqual([90000, 91000]);
    expect(opt.xAxis.data).toEqual(["2024-01-01", "2024-01-02"]);
    expect(opt.legend.data).toEqual(["D", "G"]);
  });

  it("line + seriesBy=station → 两条线, 缺格补 null", () => {
    const partial: any[][] = [
      ["D", "2024-01-01", 130996],
      ["G", "2024-01-01", 90000],
      ["G", "2024-01-02", 91000],
    ];
    const opt = buildAutoOption("line", partial, cols, "record_date", "daily_total", "station");
    const byName = Object.fromEntries(
      (opt.series as any[]).map((s) => [s.name, s.data]),
    ) as Record<string, any[]>;
    expect(byName["D"]).toEqual([130996, null]);
    expect(byName["G"]).toEqual([90000, 91000]);
  });

  it("seriesBy 列不存在 → 退化为单 series (不抛)", () => {
    const opt = buildAutoOption("bar", srows, cols, "record_date", "daily_total", "missing_col");
    const series = opt.series as any[];
    expect(series).toHaveLength(1);
  });
});
