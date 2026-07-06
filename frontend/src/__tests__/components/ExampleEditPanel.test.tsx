/* ════════════════════════════════════════════════════════════════════════════
 *  ExampleEditPanel — example 类型 KE 编辑面板单测
 *  覆盖: 5 个字段渲染 / question_pattern 编辑回调 / result_summary 编辑 / final_query_plan readOnly
 * ══════════════════════════════════════════════════════════════════════════ */

import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import ExampleEditPanel, { type ExamplePayload } from "@/components/audit/ExampleEditPanel";

const basePayload: ExamplePayload = {
  question_pattern: "统计品牌名称包含A级的品牌数量",
  question: "统计品牌名称包含A级的品牌数量",       // legacy fallback
  collections: ["shop_db.products"],
  join_keys: [],
  final_query_plan: {
    steps: [{
      db_type: "mongodb", collection: "products",
      query: { pipeline: [{ $match: { auditStatus: 0 } }] },
    }],
  },
  result_summary: "在 products 上按名称过滤统计数量",
  // old fields preserved for passthrough
  target_collection: "products",
  target_database: "shop_db",
  query_json: { pipeline: [{ $match: { auditStatus: 0 } }] },
};

describe("ExampleEditPanel", () => {
  it("渲染所有 5 个字段", () => {
    render(<ExampleEditPanel value={basePayload} onChange={() => {}} />);
    expect(screen.getByDisplayValue("统计品牌名称包含A级的品牌数量")).toBeInTheDocument();
    expect(screen.getByDisplayValue("在 products 上按名称过滤统计数量")).toBeInTheDocument();
    expect(screen.getByText("shop_db.products")).toBeInTheDocument();
    // join_keys is empty → shows "(空)"
    expect(screen.getByText("(空)")).toBeInTheDocument();
    // final_query_plan rendered as readonly textarea (contains $match from the plan)
    expect(screen.getByDisplayValue(/\$match/)).toBeInTheDocument();
  });

  it("question_pattern 可编辑触发 onChange", () => {
    const onChange = vi.fn();
    render(<ExampleEditPanel value={basePayload} onChange={onChange} />);
    fireEvent.change(screen.getByDisplayValue("统计品牌名称包含A级的品牌数量"), {
      target: { value: "新问题" },
    });
    expect(onChange).toHaveBeenCalledWith({
      ...basePayload,
      question_pattern: "新问题",
    });
  });

  it("result_summary 可编辑触发 onChange", () => {
    const onChange = vi.fn();
    render(<ExampleEditPanel value={basePayload} onChange={onChange} />);
    fireEvent.change(screen.getByDisplayValue("在 products 上按名称过滤统计数量"), {
      target: { value: "新摘要" },
    });
    expect(onChange).toHaveBeenCalledWith({
      ...basePayload,
      result_summary: "新摘要",
    });
  });

  it("final_query_plan 视图为 readOnly", () => {
    render(<ExampleEditPanel value={basePayload} onChange={() => {}} />);
    const ta = screen.getByDisplayValue(/\$match/);
    expect(ta).toHaveAttribute("readonly");
  });
});
