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
