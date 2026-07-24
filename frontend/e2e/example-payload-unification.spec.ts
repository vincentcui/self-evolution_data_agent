/* ════════════════════════════════════════════════════════════════════════════
 *  E2E: Example payload unification — 5-field CRUD + display verification
 *
 *  Covers: CreateKnowledgeForm (example) → audit queue display →
 *          ExampleEditPanel (5 fields) → EditCanonicalForm submit
 *
 *  Auth:  Real login (admin / admin123456) via _rbac_helpers — exercises real
 *         backend parse_payload gate (ExamplePayload extra='forbid', D6 不兼容:
 *         old shape → 422, new 5-field shape → 201).
 *  Browser: chromium-only — validates business path + API contract, not CSS.
 *
 *  NOTE: Requires full backend + frontend stack running. Data is created/modified
 *  on namespace 1 (must exist).
 * ══════════════════════════════════════════════════════════════════════════ */

import { test, expect } from "@playwright/test";
import { login } from "./_rbac_helpers";

// ── Test data ──
const EXAMPLE_PAYLOAD_5FIELD = {
  question_pattern: "按类型统计某库中的商家数量",
  collections: ["shop_db.merchants"],
  join_keys: [] as Record<string, unknown>[],
  final_query_plan: {
    steps: [{
      db_type: "mysql",
      database: "shop_db",
      collection: "merchants",
      operation: "sql",
      query: { sql: "SELECT type, COUNT(*) FROM merchants GROUP BY type" },
    }],
  },
  result_summary: "在 merchants 表上按 type 分组统计各类型商家数量",
};

// ── chromium-only: 业务路径 + API 契约, 非 CSS/富 UI (per e2e/CLAUDE.md) ──
test.skip(({ browserName }) => browserName !== "chromium");

test.describe("Example payload unification e2e", () => {

  test.beforeEach(async ({ page }) => {
    // ── L4: Reachability from app entry point (Home URL) ──
    await login(page, "admin");
    // Go through sidebar navigation — proves component is MOUNTED, not just URL-accessible
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    // Sidebar: "知识库" / "Knowledge" link
    const knowledgeLink = page.getByRole("link", { name: /知识库|Knowledge/i });
    await expect(knowledgeLink).toBeVisible({ timeout: 3000 });
    await knowledgeLink.click();
    await page.waitForURL(/\/knowledge/, { timeout: 5000 });
    await page.waitForLoadState("networkidle");
  });

  test("L1: CreateKnowledgeForm → example with 5 new fields", async ({ page }) => {
    // ── Open create modal ──
    await page.getByRole("button", { name: /添加/ }).click();
    const modal = page.getByRole("dialog");
    await expect(modal).toBeVisible({ timeout: 3000 });

    // ── Select "example" type ──
    await modal.locator(".ant-select").first().click();
    await page.getByText(/示例查询.*example/).click();

    // ── Fill 5-field form ──
    await modal.getByLabel("question_pattern").fill(EXAMPLE_PAYLOAD_5FIELD.question_pattern);
    await modal.getByLabel("collections").fill("shop_db.merchants");
    await modal.getByLabel("final_query_plan").fill(
      JSON.stringify(EXAMPLE_PAYLOAD_5FIELD.final_query_plan),
    );
    await modal.getByLabel("result_summary").fill(EXAMPLE_PAYLOAD_5FIELD.result_summary);

    // ── Submit ──
    await modal.getByRole("button", { name: /确定|OK/ }).click();

    // ── Verify success (no error message) ──
    await expect(page.getByText(/格式不合法/)).not.toBeVisible({ timeout: 2000 });
    // The modal should close on success
    await expect(modal).not.toBeVisible({ timeout: 5000 });
  });

  test("L2: ExampleEditPanel — 5 fields visible in edit form", async ({ page }) => {
    // ── Find the created entry in the queue ──
    // Switch to "全部" or find by content
    const row = page.getByText(EXAMPLE_PAYLOAD_5FIELD.question_pattern);
    await expect(row.first()).toBeVisible({ timeout: 5000 }); // fine

    // ── Click edit ──
    const editBtn = page.getByRole("button", { name: /编辑|编 辑/ }).first();
    await editBtn.click();

    // ── Verify 5 fields present ──
    // question_pattern (editable)
    const qpInput = page.getByLabel("question_pattern");
    await expect(qpInput).toBeVisible({ timeout: 3000 });
    await expect(qpInput).toHaveValue(EXAMPLE_PAYLOAD_5FIELD.question_pattern);

    // result_summary (editable)
    const rsInput = page.getByLabel("result_summary");
    await expect(rsInput).toBeVisible({ timeout: 1000 });
    await expect(rsInput).toHaveValue(EXAMPLE_PAYLOAD_5FIELD.result_summary);

    // collections (read-only, displayed as tags)
    await expect(page.getByText("shop_db.merchants").first()).toBeVisible({ timeout: 1000 });

    // join_keys (read-only, empty → shows "(空)")
    await expect(page.getByText("(空)").first()).toBeVisible({ timeout: 1000 });

    // final_query_plan (read-only, rendered as JSON textarea)
    const planTextarea = page.getByLabel("final_query_plan");
    await expect(planTextarea).toBeVisible({ timeout: 1000 });
    const planValue = await planTextarea.inputValue();
    expect(JSON.parse(planValue)).toEqual(EXAMPLE_PAYLOAD_5FIELD.final_query_plan);
  });

  test("L3: EditCanonicalForm submit with new fields → 200", async ({ page }) => {
    // ── Find and edit the entry ──
    const row = page.getByText(EXAMPLE_PAYLOAD_5FIELD.question_pattern);
    await expect(row.first()).toBeVisible({ timeout: 5000 }); // fine

    const editBtn = page.getByRole("button", { name: /编辑|编 辑/ }).first();
    await editBtn.click();

    // ── Edit question_pattern ──
    const qpInput = page.getByLabel("question_pattern");
    await qpInput.clear();
    await qpInput.fill("按类型统计某库中的商家数量 V2");

    // ── Fill reason and save ──
    await page.getByPlaceholder("为何修改").fill("e2e test edit");

    // ── Intercept PUT to verify response status — exact pattern, not broad match ──
    const putPromise = page.waitForResponse(
      (r) => /\/api\/knowledge\/\d+$/.test(r.url()) && r.request().method() === "PUT",
      { timeout: 10000 },
    );

    await page.getByRole("button", { name: /^保.?存$/ }).click();
    const putResp = await putPromise;
    expect(putResp.status()).toBe(200);

    // ── Verify success message ──
    await expect(page.getByText("已编辑")).toBeVisible({ timeout: 3000 });
  });

  test("L4: D6 不兼容 — old-shape payload 422, new-shape 201 + renders", async ({
    page, request,
  }) => {
    // ── D6 "不迁移不兼容" 策略锁定 ──
    // 旧 shape (question / target_collection / query_json, 缺 final_query_plan)
    // 被 ExamplePayload(extra="forbid") + final_query_plan 必填拒绝 → 422.
    // 新 shape (question_pattern + final_query_plan) → 201 且 UI 正常渲染.
    const loginResp = await request.post("/api/auth/login", {
      data: { username: "admin", password: "admin123456" },
    });
    const token = (await loginResp.json()).access_token;

    // ── Old shape: unknown fields + missing final_query_plan → 422 ──
    const oldShapeResp = await request.post("/api/knowledge", {
      headers: { Authorization: `Bearer ${token}` },
      data: {
        entry_type: "example",
        namespace_id: 1,
        tier: "normal",
        content: "查看各订单状态的数量分布",
        payload: {
          question: "查看各订单状态的数量分布",
          target_collection: "orders",
          target_database: "shop",
          query_json: { pipeline: [{ $group: { _id: "$status", count: { $sum: 1 } } }] },
          result_summary: "在 orders 上按 status 字段 $group + $sum:1",
        },
      },
    });
    expect(oldShapeResp.status()).toBe(422);

    // ── New shape: question_pattern + final_query_plan → 201 ──
    const newQp = "按订单状态统计数量分布 " + Date.now();
    const newShapeResp = await request.post("/api/knowledge", {
      headers: { Authorization: `Bearer ${token}` },
      data: {
        entry_type: "example",
        namespace_id: 1,
        tier: "normal",
        content: newQp,
        payload: {
          question_pattern: newQp,
          collections: [{ database: "shop", collection: "orders" }],
          join_keys: [],
          final_query_plan: {
            steps: [{
              db_type: "mongodb",
              database: "shop",
              collection: "orders",
              operation: "aggregate",
              query: { pipeline: [{ $group: { _id: "$status", count: { $sum: 1 } } }] },
            }],
          },
          result_summary: "在 orders 上按 status 字段 $group + $sum:1",
        },
      },
    });
    expect(newShapeResp.status()).toBe(201);

    // ── New-shape entry renders in the knowledge queue ──
    await page.goto("/knowledge");
    await page.waitForLoadState("networkidle");

    const newRow = page.getByText(newQp);
    await expect(newRow.first()).toBeVisible({ timeout: 5000 });

    // ── Edit panel shows new-shape fields (question_pattern + final_query_plan) ──
    const editBtn = page.getByRole("button", { name: /编辑|编 辑/ }).first();
    await editBtn.click();

    const qpInput = page.getByLabel("question_pattern");
    await expect(qpInput).toHaveValue(newQp, { timeout: 3000 });

    const planTextarea = page.getByLabel("final_query_plan");
    await expect(planTextarea).toBeVisible({ timeout: 3000 });
    const planJson = JSON.parse(await planTextarea.inputValue());
    expect(planJson.steps[0].collection).toBe("orders");
  });

  test("L5: New fields survive round-trip (create → edit → verify)", async ({
    page,
  }) => {
    // ── Create via CreateKnowledgeForm ──
    await page.getByRole("button", { name: /添加/ }).click();
    const modal = page.getByRole("dialog");
    await modal.locator(".ant-select").first().click();
    await page.getByText(/示例查询.*example/).click();

    const testQp = "某数据库下按类型分组统计" + Date.now();
    await modal.getByLabel("question_pattern").fill(testQp);
    await modal.getByLabel("collections").fill("shop.orders");
    await modal.getByLabel("final_query_plan").fill(
      JSON.stringify({
        steps: [{
          db_type: "mysql", database: "shop", collection: "orders", operation: "sql",
          query: { sql: "SELECT status, COUNT(*) FROM orders GROUP BY status" },
        }],
      }),
    );
    await modal.getByLabel("result_summary").fill("按状态分组统计");
    await modal.getByRole("button", { name: /确定|OK/ }).click();
    await expect(modal).not.toBeVisible({ timeout: 5000 });

    // ── Find entry and edit ──
    await page.waitForLoadState("networkidle");
    const createdRow = page.getByText(testQp);
    await expect(createdRow.first()).toBeVisible({ timeout: 5000 });

    const editBtn = page.getByRole("button", { name: /编辑|编 辑/ }).first();
    await editBtn.click();

    // ── Verify all fields survived round-trip ──
    await expect(page.getByLabel("question_pattern")).toHaveValue(testQp);
    const planTextarea = page.getByLabel("final_query_plan");
    const planJson = JSON.parse(await planTextarea.inputValue());
    expect(planJson.steps[0].collection).toBe("orders");
  });
});
