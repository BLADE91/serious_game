import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { access, mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { expectedMainEndingIds } from "./acceptance-evidence.js";

const viewports = [
  { name: "desktop-1920", width: 1920, height: 1080 },
  { name: "laptop-1366", width: 1366, height: 768 },
  { name: "mobile-390", width: 390, height: 844 },
];
const enabled = process.env.RUN_FULL_REAL_E2E === "1";
const evidenceRoot = process.env.FULL_ACCEPTANCE_BROWSER_DIR
  ? path.resolve(process.env.FULL_ACCEPTANCE_BROWSER_DIR)
  : path.resolve(process.cwd(), "../../../output/full-acceptance/playwright");
const routeCatalogPath = path.resolve(process.cwd(), "../../backend/content/packages/pkg_gameplay_v3/acceptance_route_profiles.json");
const shardIndex = Number(process.env.FULL_E2E_SHARD_INDEX || 0);
const shardTotal = Math.max(1, Number(process.env.FULL_E2E_SHARD_TOTAL || 1));

async function assertNoHorizontalOverflow(page: Page) {
  const dimensions = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth + 1);
}

async function assertPortraitDoesNotCoverText(page: Page) {
  const overlap = await page.evaluate(() => {
    const portrait = document.querySelector(".gal-portrait")?.getBoundingClientRect();
    const text = document.querySelector(".gal-dialogue p")?.getBoundingClientRect();
    if (!portrait || !text) return 0;
    const width = Math.max(0, Math.min(portrait.right, text.right) - Math.max(portrait.left, text.left));
    const height = Math.max(0, Math.min(portrait.bottom, text.bottom) - Math.max(portrait.top, text.top));
    return width * height;
  });
  expect(overlap).toBe(0);
}

async function capture(page: Page, testInfo: TestInfo, state: string) {
  await assertNoHorizontalOverflow(page);
  await assertPortraitDoesNotCoverText(page);
  await page.screenshot({ path: testInfo.outputPath(`${state}.png`), fullPage: true });
}

async function writeBrowserEvidence(testInfo: TestInfo, consoleEvents: unknown[], networkEvents: unknown[], summary: unknown) {
  const folder = testInfo.outputPath("visual-evidence");
  await mkdir(folder, { recursive: true });
  await writeFile(path.join(folder, "console.json"), JSON.stringify(consoleEvents, null, 2));
  await writeFile(path.join(folder, "network.json"), JSON.stringify(networkEvents, null, 2));
  await writeFile(path.join(folder, "browser-summary.json"), JSON.stringify(summary, null, 2));
}

test.describe("authoritative visual matrix", () => {
  test.skip(!enabled, "visual acceptance is part of the final real run only");
  for (const viewport of viewports) {
    test(`${viewport.name} player-visible state inventory`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      const consoleEvents: unknown[] = [];
      const networkEvents: unknown[] = [];
      page.on("console", message => { if (message.type() === "error") consoleEvents.push({ type: message.type(), text: message.text() }); });
      page.on("pageerror", error => consoleEvents.push({ type: "pageerror", text: error.message }));
      page.on("requestfailed", request => networkEvents.push({ method: request.method(), path: new URL(request.url()).pathname, failure: request.failure()?.errorText }));

      await page.goto("/");
      await capture(page, testInfo, "login-api"); // visual:login-api
      const manifestPath = path.join(evidenceRoot, "browser-state-manifest.jsonl");
      const records = (await readFile(manifestPath, "utf8")).trim().split(/\r?\n/).filter(Boolean).map(line => JSON.parse(line));
      const routeCatalog = JSON.parse(await readFile(routeCatalogPath, "utf8"));
      const expectedEndings = expectedMainEndingIds(routeCatalog.profiles, shardIndex, shardTotal);
      const forViewport = records.filter(item => item.viewport === viewport.name);
      const states = new Set(forViewport.map(item => item.state));
      for (const state of [
        "today", // visual:today
        "people", // visual:people
        "governance", // visual:governance
        "archive-result", // visual:archive-result
        "clues", // visual:clues
        "map-8-locations", // visual:map-8-locations
        "contract-3-stages", // visual:contract-3-stages
        "leadership-meeting", // visual:leadership-meeting
        "morning-briefing", // visual:morning-briefing
        "save-load", // visual:save-load
        "review", // visual:review
      ]) expect(states, `${state} has no screenshot at ${viewport.name}`).toContain(state);
      const forcedPlans = new Set(forViewport.filter(item => item.state === "forced-conversation").map(item => item.plan_id)); // visual:forced-conversation-6
      expect(forcedPlans.size).toBe(6);
      const mainEndings = new Set(forViewport.filter(item => item.state === "main-ending").flatMap(item => item.main_ending_ids || [])); // visual:main-endings-24
      expect([...mainEndings].sort()).toEqual(expectedEndings);
      expect(forViewport.filter(item => item.state === "map-8-locations").every(item => item.location_count === 8)).toBe(true);
      await Promise.all(forViewport.map(item => access(item.screenshot)));
      await writeBrowserEvidence(testInfo, consoleEvents, networkEvents, {
        viewport,
        state_count: states.size,
        screenshot_count: forViewport.length,
        forced_plan_count: forcedPlans.size,
        main_ending_count: mainEndings.size,
        source: "ui-created-session",
      });
      expect(consoleEvents).toEqual([]);
    });
  }
});
