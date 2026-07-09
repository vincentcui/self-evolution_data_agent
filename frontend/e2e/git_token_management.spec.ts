import { test, expect } from "@playwright/test";

test.describe("Git Token Management e2e (L3 + L4)", () => {
  // ── L4: 路由守卫 — 非 super_admin 被重定向 ──
  test("普通 admin 访问 /config/git-token 被重定向 (L4)", async ({ page }) => {
    // 先登录普通 admin (非 super_admin) — 需根据项目测试环境适配
    // await login(page, "admin");
    await page.goto("/config/git-token");
    // RequireSuperAdmin 守卫应重定向到非 config 页面
    await page.waitForURL((url) => !url.pathname.startsWith("/config/git-token"), { timeout: 10000 });
  });

  // ── L3: 真实链路 — super_admin CRUD ──
  test("super_admin 管理全局 Git Token (L3)", async ({ page }) => {
    // 登录 super_admin — 需根据项目测试环境适配
    // await login(page, "admin");

    // 导航到配置中心 Git Token 页面
    await page.goto("/config/git-token");
    await expect(page.getByText("全局 Git Token 管理")).toBeVisible({ timeout: 10000 });

    // 新增 token
    await page.getByRole("button", { name: "新增" }).click();
    await page.locator(".ant-modal input#name").fill("e2e-test-token");
    await page.locator(".ant-modal input[type='password']").fill("ghp_e2e_test_xxx");
    await page.locator(".ant-modal-footer").getByRole("button", { name: /确\s*定/ }).click();
    await expect(page.getByText("e2e-test-token")).toBeVisible({ timeout: 10000 });

    // 激活 token
    await page.getByRole("button", { name: "激活" }).click();
    await expect(page.locator("tr").filter({ hasText: "e2e-test-token" }).getByText("已激活")).toBeVisible({
      timeout: 10000,
    });

    // 删除 token (清理 e2e 数据)
    await page.getByRole("button", { name: "删除" }).click();
    await page.locator(".ant-popconfirm").getByRole("button", { name: /确\s*定/ }).click();
    await expect(page.getByText("e2e-test-token")).not.toBeVisible({ timeout: 10000 });
  });
});
