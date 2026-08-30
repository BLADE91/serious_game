import { chromium, type FullConfig } from "@playwright/test";
import { appendFile, mkdir } from "node:fs/promises";
import path from "node:path";

const viewports = [
  { name: "desktop-1920", width: 1920, height: 1080 },
  { name: "laptop-1366", width: 1366, height: 768 },
  { name: "mobile-390", width: 390, height: 844 },
];

export default async function globalSetup(config: FullConfig) {
  if (process.env.RUN_FULL_REAL_E2E !== "1") return;
  const baseURL = String(config.projects[0]?.use?.baseURL || "http://127.0.0.1:3001");
  const storageState = process.env.FULL_E2E_STORAGE_STATE;
  const evidenceRoot = process.env.FULL_ACCEPTANCE_BROWSER_DIR;
  if (!storageState || !evidenceRoot) throw new Error("full browser acceptance paths are missing");
  await mkdir(evidenceRoot, { recursive: true });
  const browser = await chromium.launch();
  const context = await browser.newContext({ locale: "zh-CN", timezoneId: "Asia/Shanghai" });
  const page = await context.newPage();
  await page.goto(baseURL);
  const authDialog = page.getByRole("dialog").filter({ hasText: "登录治理档案" });
  await authDialog.waitFor({ state: "visible" });
  const shard = String(process.env.FULL_E2E_SHARD_INDEX || "visual").replace(/[^a-zA-Z0-9_-]/g, "");
  const username = `e2e_${shard}_${Date.now().toString(36)}`.slice(0, 32);
  const password = `E2e-${Date.now().toString(36)}-Safe!`;
  await page.getByRole("button", { name: "注册", exact: true }).click();
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码").fill(password);
  await page.getByRole("button", { name: "注册并开始" }).click();
  await page.getByRole("heading", { name: "配置 AI 接口" }).waitFor();
  const manifest = path.join(evidenceRoot, "browser-state-manifest.jsonl");
  for (const viewport of viewports) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const screenshot = path.join(evidenceRoot, `login-api-${viewport.name}.png`);
    await page.screenshot({ path: screenshot, fullPage: true });
    await appendFile(manifest, `${JSON.stringify({
      route_id: "shared-auth-bootstrap",
      state: "login-api",
      variant: "server-default",
      viewport: viewport.name,
      width: viewport.width,
      height: viewport.height,
      screenshot,
      layout: await page.evaluate(() => ({
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      })),
    })}\n`, "utf8");
  }
  const serverDefault = page.getByLabel("使用服务器默认接口");
  await serverDefault.check();
  await page.getByRole("button", { name: "启用服务器默认接口" }).click();
  await page.getByText("接口测试成功", { exact: true }).waitFor({ timeout: 600_000 });
  await page.getByRole("button", { name: "配置成功，继续" }).click();
  await context.storageState({ path: storageState });
  await browser.close();
}
