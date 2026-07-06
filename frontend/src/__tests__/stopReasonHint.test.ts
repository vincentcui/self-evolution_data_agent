// Feature: mongo-flavor-capabilities-and-error-clarify
// Property 26: 前端 stop_reason 提示映射完整性（仅警示类）; R7.2
import { describe, it, expect } from "vitest";
import { STOP_REASON_HINT } from "../components/stream/FinalResult";

describe("STOP_REASON_HINT", () => {
  const warningReasons = [
    "dead_loop",
    "stagnation",
    "cost_exhausted",
    "max_total_iterations",   // 兼容旧后端
    "forced_clarify_timeout",
    "forced_clarify_exhausted",
  ];

  it("Property 26: every warning-class stop_reason has a non-empty hint", () => {
    for (const r of warningReasons) {
      expect(STOP_REASON_HINT[r]).toBeTruthy();
      expect(STOP_REASON_HINT[r].length).toBeGreaterThan(0);
    }
  });

  it("Property 26: end_turn is NOT in the map (success has no warning)", () => {
    expect("end_turn" in STOP_REASON_HINT).toBe(false);
  });

  it("includes the two new forced_clarify reasons", () => {
    expect(STOP_REASON_HINT["forced_clarify_timeout"]).toContain("超时");
    expect(STOP_REASON_HINT["forced_clarify_exhausted"]).toBeTruthy();
  });
});
