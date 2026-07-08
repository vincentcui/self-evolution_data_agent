/* ════════════════════════════════════════════════════════════════════════════
 *  G6 验收 — 模型管理"历史轮次"配置字段
 *  CHAT 配置编辑态显示"历史轮次"输入且回填值; EMBEDDING 态隐藏。
 *  多轮上下文注入本身(G1/G2/G3)是后端 LLM messages 行为, 浏览器不可观测,
 *  由后端集成测试覆盖; 此 e2e 仅验唯一浏览器可观测门 G6。
 * ══════════════════════════════════════════════════════════════════════════ */
import { test, expect } from "@playwright/test";

const CHAT_CFG = {
  id: 1, provider: "openai", protocol: "openai", base_url: "http://x",
  api_key: "sk-****", model_name: "gpt-4o", model_type: "CHAT",
  temperature: 0.0, max_tokens: 12288, max_history_turns: 7, is_active: true,
  completions_path: null, embeddings_path: null, proxy_enabled: false,
  created_at: "2026-07-06T00:00:00", updated_at: null,
};
const EMBED_CFG = {
  ...CHAT_CFG, id: 2, model_type: "EMBEDDING", model_name: "text-embed",
  max_history_turns: 5, is_active: false,
};

async function bootstrap(page: import("@playwright/test").Page, configs: unknown[]) {
  await page.addInitScript(() => {
    localStorage.setItem("token", "fake-jwt-test-token");
    localStorage.setItem(
      "user",
      JSON.stringify({ id: 1, username: "admin", role: "super_admin", email: "a@e2e" }),
    );
  });
  await page.route("**/api/model-config/list", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(configs) }),
  );
  await page.route("**/api/model-config/check-ready", (route) =>
    route.fulfill({
      status: 200, contentType: "application/json",
      body: JSON.stringify({ chat_model_ready: true, embedding_model_ready: true, ready: true }),
    }),
  );
  await page.goto("/workspace/model-management");
}

test.describe("model management 历史轮次 (G6)", () => {
  test("CHAT 配置编辑态显示历史轮次并回填", async ({ page }) => {
    await bootstrap(page, [CHAT_CFG]);
    // 编辑触发: 行内操作列的"编辑"按钮 (ModelManagement/index.tsx styles.actEdit)
    await page.getByRole("button", { name: "编辑" }).click();
    await expect(page.getByText("历史轮次")).toBeVisible({ timeout: 2000 });
    // 回填值校验: 定位"历史轮次"标签同行的 number input(Playwright 无
    // Testing-Library 式 getByDisplayValue, 用 xpath 走 label→同行 input)
    const historyTurnsInput = page.locator(
      "xpath=//label[contains(., '历史轮次')]/following-sibling::div//input",
    );
    await expect(historyTurnsInput).toHaveValue("7");
  });

  test("EMBEDDING 配置编辑态隐藏历史轮次", async ({ page }) => {
    await bootstrap(page, [EMBED_CFG]);
    await page.getByRole("button", { name: "编辑" }).click();
    // 正向锚点: 证明表单已打开且渲染了 EMBEDDING 分支 (ModelForm.tsx embedHint),
    // 否则下方 toHaveCount(0) 在"表单根本没打开"时也会误判通过 (vacuous pass)。
    await expect(page.getByText("嵌入模型无需配置温度和 Token 参数。")).toBeVisible({ timeout: 2000 });
    await expect(page.getByText("历史轮次")).toHaveCount(0);
  });
});
