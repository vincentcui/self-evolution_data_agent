/* ════════════════════════════════════════════════════════════════════════════
 *  Stage 2 抓手 E — Playwright e2e: Agent Traces 页面
 * ----------------------------------------------------------------------------
 *  Mock /api/agent-traces 验业务路径:
 *    1. 列表渲染 + status 过滤
 *    2. 详情 modal 展示 reflection_log
 *    3. 批量提炼按钮调用
 *  baseURL = http://localhost:3001 (vite preview), Chromium project.
 * ══════════════════════════════════════════════════════════════════════════ */

import { test, expect } from "@playwright/test";

async function setupAgentTracesPage(page: import("@playwright/test").Page) {
  await page.addInitScript(() => {
    localStorage.setItem("token", "fake-jwt-test-token");
    localStorage.setItem(
      "user",
      JSON.stringify({
        id: 1,
        username: "admin",
        role: "admin",
        is_active: true,
      }),
    );
  });

  await page.route("**/api/users/me", (r) =>
    r.fulfill({
      json: {
        id: 1,
        username: "admin",
        role: "admin",
        is_active: true,
        created_at: "2026-01-01",
      },
    }),
  );

  await page.route("**/api/namespaces", (r) =>
    r.fulfill({
      json: [
        { id: 1, name: "测试空间", slug: "test", description: "", created_at: "2026-01-01" },
      ],
    }),
  );
}

const makeTrace = (id: number, traceId: string, query: string, status: string) => ({
  id,
  trace_id: traceId,
  namespace_id: 1,
  user_query: query,
  status,
  created_at: "2026-05-20T10:00:00Z",
  tool_call_count: 3,
});

test.describe("agent traces e2e", () => {
  test("table renders, status filter works, detail modal shows reflection, refine works", async ({ page }) => {
    await setupAgentTracesPage(page);

    const traces = [
      makeTrace(1, "t-001", "本月活跃用户数", "completed"),
      makeTrace(2, "t-002", "订单总金额", "completed"),
      makeTrace(3, "t-003", "失败的查询", "failed"),
    ];

    await page.route("**/api/agent-traces?**", (r) => {
      const url = new URL(r.request().url());
      const statusParam = url.searchParams.get("status");
      const filtered = statusParam
        ? traces.filter((t) => t.status === statusParam)
        : traces;
      return r.fulfill({ json: filtered });
    });

    await page.route("**/api/agent-traces/t-001", (r) =>
      r.fulfill({
        json: {
          id: 1,
          trace_id: "t-001",
          namespace_id: 1,
          user_query: "本月活跃用户数",
          trace_json: '{"tool_trace": [{"name": "lookup_knowledge", "input": {}, "output": {}, "status": "ok"}]}',
          reflection_log_json: JSON.stringify([
            { tool_name: "lookup_knowledge", confidence: 0.85, reason: "匹配到规则", alternative: "" },
          ]),
          tool_trace_compact: [{ step: 0, tool: "lookup_knowledge" }],
          status: "completed",
          refined_at: null,
          refined_summary: null,
          created_at: "2026-05-20T10:00:00Z",
        },
      }),
    );

    let refineCalled = false;
    await page.route("**/api/agent-traces/refine", (r) => {
      refineCalled = true;
      return r.fulfill({
        json: { proposed_count: 2, proposed_ke_ids: [1, 2] },
      });
    });

    await page.goto("/admin/agent-traces");

    // ── 验表格渲染 ──
    await expect(page.getByText("本月活跃用户数")).toBeVisible({ timeout: 5000 });
    await expect(page.getByText("订单总金额")).toBeVisible();
    await expect(page.getByText("失败的查询")).toBeVisible();

    // ── 详情 modal ──
    await page.getByRole("button", { name: "详情" }).first().click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible({ timeout: 3000 });
    // 调用列表表头 (新结构)
    await expect(dialog.getByText("调用列表")).toBeVisible();
    // 入参/返回值 列标题 (新结构, 替换旧 查询摘要/结果)
    await expect(dialog.getByText("入参")).toBeVisible();
    await expect(dialog.getByText("返回值")).toBeVisible();
    // lookup_knowledge 现来自调用列表 (tool_trace_compact), 非旧 reflection 表
    await expect(dialog.getByText("lookup_knowledge").first()).toBeVisible();
    // reflection 覆盖层: mock reflection 非空, 0.85 / 匹配到规则 仍渲染 (现验覆盖层, 非旧面板)
    await expect(dialog.getByText("0.85")).toBeVisible();
    await expect(dialog.getByText("匹配到规则")).toBeVisible();
    await dialog.locator("button.ant-modal-close").click();

    // ── 批量提炼 ──
    // 选择 completed 行 (checkbox)
    const checkboxes = page.locator(".ant-checkbox-input");
    await checkboxes.first().check({ force: true });

    await page.getByRole("button", { name: /批量提炼/ }).click();
    await expect.poll(() => refineCalled, { timeout: 3000 }).toBeTruthy();
    await expect(page.getByText(/产生 2 条/)).toBeVisible({ timeout: 3000 });
  });
});
