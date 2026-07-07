/* ════════════════════════════════════════════
 *  L3: namespace 模型配置 e2e — page.route + DB 断言
 * ════════════════════════════════════════════ */
import { test, expect } from "@playwright/test";
import { login } from "./_rbac_helpers";

test("L3: list API URL 精确匹配 namespace_id 查询参数", async ({ page }) => {
  let capturedUrl = "";

  await page.route("**/api/model-config/list?namespace_id=*", (route, request) => {
    capturedUrl = request.url();
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
  });

  await login(page, "admin");
  // 导航到工作台 → 命名空间 → 模型配置 Tab (触发 listModelConfigs(nsId))
  await page.click('button:has-text("工作台")');
  await page.waitForURL("**/workspace/**");
  await page.click('.ant-tabs-tab:has-text("模型配置")');

  expect(capturedUrl).toContain("namespace_id=");
  expect(capturedUrl).not.toContain("namespace_id=null");
});

test("L3: 激活后 DB 状态变更 — is_active 唯一", async ({ page, request }) => {
  await login(page, "admin");

  // 获取 token 用于 API 调用
  const loginResp = await request.post("/api/auth/login", {
    data: { username: "admin", password: "admin123456" },
  });
  const token = (await loginResp.json()).access_token;
  const headers = { Authorization: `Bearer ${token}` };

  // 创建 namespace
  const nsResp = await request.post("/api/namespaces", {
    data: { name: "e2e-l3-test", slug: "e2e-l3-test", description: "" },
    headers,
  });
  expect(nsResp.status()).toBe(201);
  const nsId = (await nsResp.json()).id;

  // 创建两条 CHAT config
  const ids: number[] = [];
  for (const i of [0, 1]) {
    const resp = await request.post("/api/model-config/add", {
      data: {
        provider: "openai", base_url: `https://api${i}.openai.com`,
        api_key: `sk-${i}`, model_name: `model-${i}`,
        model_type: "CHAT", namespace_id: nsId,
      },
      headers,
    });
    ids.push((await resp.json()).id);
  }

  // 激活第一条
  const actResp = await request.post(`/api/model-config/activate/${ids[0]}`, undefined, { headers });
  expect(actResp.status()).toBe(200);

  // 断言：该 namespace 下仅一个 active
  const listResp = await request.get(`/api/model-config/list?namespace_id=${nsId}`, { headers });
  const configs = await listResp.json();
  const activeCount = configs.filter((c: any) => c.is_active).length;
  expect(activeCount).toBe(1);
});
