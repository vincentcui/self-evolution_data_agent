/**
 * E2E: relationship add/edit flow — 真实登录, 零 API mock.
 *
 * Browser scope: chromium-only
 * Prerequisites: backend :8001, frontend :3000|:3001
 * Admin: admin / admin123456
 * 至少一个 namespace + 至少一条已 introspect 的 SCO
 */
import { test, expect } from "@playwright/test";
import { login } from "./_rbac_helpers";

async function selectFirstNamespace(page: any) {
  await page.goto("/knowledge");
  const nsSelect = page.locator(".ant-select").first();
  await nsSelect.click();
  const firstNs = page.locator(
    ".ant-select-dropdown:visible .ant-select-item-option",
  ).first();
  await expect(firstNs).toBeVisible({ timeout: 5000 });
  await firstNs.click();
  await page.waitForTimeout(1000);
}

async function goToSchemaAndSelectSco(page: any) {
  await page.getByRole("button", { name: /Schema 管理/ }).click();
  const scoSel = page.locator(".ant-select").nth(1);
  await expect(scoSel).toBeVisible({ timeout: 5000 });
  await scoSel.click();
  const firstSco = page.locator(
    ".ant-select-dropdown:visible .ant-select-item-option",
  ).first();
  if (await firstSco.isVisible({ timeout: 3000 }).catch(() => false)) {
    await firstSco.click();
    await page.waitForTimeout(500);
  }
}

test.describe("relationship edit flow", () => {
  test.beforeEach(async ({ page }) => {
    await login(page, "admin");
  });

  test("navigate to Schema tab and see relationship heading", async ({
    page,
  }) => {
    await selectFirstNamespace(page);
    await goToSchemaAndSelectSco(page);
    // heading always visible (with count 0 when no relationships)
    await expect(page.getByRole("heading", { name: /关联关系/ })).toBeVisible({ timeout: 8000 });
  });

  test("edit mode: add relationship row with inline inputs", async ({
    page,
  }) => {
    await selectFirstNamespace(page);
    await goToSchemaAndSelectSco(page);

    // Enter edit mode
    await page.getByRole("button", { name: /编辑 Schema/ }).click();

    // "添加关联关系" button visible
    const addBtn = page.getByRole("button", { name: /添加关联关系/ });
    await expect(addBtn).toBeVisible({ timeout: 3000 });
    await addBtn.click();
    await page.waitForTimeout(300);

    // Verify inline input row appeared
    const relRows = page.locator(".rel-edit-row");
    const rowCount = await relRows.count();
    expect(rowCount).toBeGreaterThanOrEqual(1);

    // Fill from_field (first Input in the row)
    const rowInputs = relRows.first().locator(".ant-input");
    await rowInputs.nth(0).fill("user_id");
    await rowInputs.nth(2).fill("mysql");
    await rowInputs.nth(4).fill("t_user");
    await rowInputs.nth(5).fill("id");

    // Verify input values
    await expect(rowInputs.nth(0)).toHaveValue("user_id");
    await expect(rowInputs.nth(4)).toHaveValue("t_user");
  });

  test("edit mode: add then delete relationship row", async ({ page }) => {
    await selectFirstNamespace(page);
    await goToSchemaAndSelectSco(page);

    await page.getByRole("button", { name: /编辑 Schema/ }).click();
    await page.getByRole("button", { name: /添加关联关系/ }).click();
    await page.waitForTimeout(300);

    // Count relationship delete buttons (scoped to .rel-del-btn)
    const before = await page.locator(".rel-del-btn").count();
    expect(before).toBeGreaterThanOrEqual(1);

    // Click first relationship delete button
    await page.locator(".rel-del-btn").first().click();
    await page.waitForTimeout(300);

    const after = await page.locator(".rel-del-btn").count();
    expect(after).toBe(before - 1);
  });

  test("styles: layout verified — 添加字段 before 添加关联关系", async ({
    page,
  }) => {
    await selectFirstNamespace(page);
    await goToSchemaAndSelectSco(page);

    await page.getByRole("button", { name: /编辑 Schema/ }).click();

    const addFieldBtn = page.getByRole("button", { name: /添加字段/ });
    const addRelBtn = page.getByRole("button", { name: /添加关联关系/ });
    await expect(addFieldBtn).toBeVisible({ timeout: 3000 });
    await expect(addRelBtn).toBeVisible({ timeout: 3000 });

    // 添加字段 在 DOM 中应出现在 添加关联关系 之前
    const fieldBox = await addFieldBtn.boundingBox();
    const relBox = await addRelBtn.boundingBox();
    expect(fieldBox).not.toBeNull();
    expect(relBox).not.toBeNull();
    if (fieldBox && relBox) {
      expect(fieldBox.y).toBeLessThan(relBox.y);
    }

    // Click 添加关联关系 → verify inputs render in viewport
    await addRelBtn.click();
    await page.waitForTimeout(300);

    const inputs = page.locator(".ant-input");
    expect(await inputs.count()).toBeGreaterThanOrEqual(4);
    const firstBox = await inputs.first().boundingBox();
    expect(firstBox).not.toBeNull();
    if (firstBox) {
      expect(firstBox.x).toBeGreaterThanOrEqual(0);
    }
  });
});
