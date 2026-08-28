import { defineConfig } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const evidenceRoot = process.env.FULL_ACCEPTANCE_BROWSER_DIR
  ? path.resolve(process.env.FULL_ACCEPTANCE_BROWSER_DIR)
  : path.resolve(here, "../../../output/full-acceptance/playwright");

export default defineConfig({
  testDir: "./e2e",
  outputDir: path.join(evidenceRoot, "artifacts"),
  fullyParallel: false,
  workers: 1,
  forbidOnly: true,
  retries: 0,
  timeout: 7_200_000,
  expect: { timeout: 30_000 },
  reporter: [
    ["line"],
    ["json", { outputFile: path.join(evidenceRoot, "playwright-report.json") }],
    ["html", { outputFolder: path.join(evidenceRoot, "html-report"), open: "never" }],
  ],
  use: {
    baseURL: process.env.FULL_ACCEPTANCE_BASE_URL || "http://127.0.0.1:3001",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    colorScheme: "dark",
    reducedMotion: "reduce",
  },
  projects: [{ name: "chromium", use: { browserName: "chromium" } }],
});
