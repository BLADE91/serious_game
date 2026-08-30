import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

test("full browser acceptance is wired through every player-visible system", () => {
  const source = read("../e2e/full-game.spec.ts");
  for (const marker of [
    "login",
    "api-configuration",
    "narrative",
    "people",
    "governance",
    "archives",
    "clues",
    "map",
    "contract",
    "meeting",
    "night",
    "save-load",
    "review",
    "ending",
  ]) {
    assert.match(source, new RegExp(`acceptance:${marker}`));
  }
  assert.match(source, /acceptance_route_profiles\.json/);
  assert.match(source, /inspectAllAvailableArchives/);
  assert.match(source, /signContractsTowardTarget/);
  assert.match(source, /exerciseMapAction/);
  assert.match(source, /exerciseManualSaveLoad/);
  assert.match(source, /\/manual-saves/);
  assert.match(source, /\/load-snapshot/);
  assert.match(source, /catalog\.contract_terms/);
  assert.match(source, /credibleForcedReplies/);
  assert.match(source, /selectedReply/);
  assert.match(source, /toHaveValue\(selectedReply\)/);
  assert.equal((source.match(/expect\(input\)\.toBeDisabled/g) || []).length, 3);
  assert.match(source, /waitForCommittedState/);
  assert.match(source, /data-state-version/);
  assert.match(source, /asMap\(payload\.state\)\.state_version/);
  assert.match(source, /streamErrorCode/);
  assert.match(source, /retryable stream failure must not change state/);
  for (const topic of ["迁坟", "材料", "环保", "公开", "巡察"]) assert.match(source, new RegExp(topic));
  assert.match(source, /page\.getByRole|page\.getByText|page\.locator/);
  assert.doesNotMatch(source, /request\.(post|put|patch|delete)\(/);
});

test("visual matrix fixes the three authoritative viewport sizes and evidence states", () => {
  const source = read("../e2e/visual-matrix.spec.ts");
  assert.match(source, /desktop-1920[^\n]+1920[^\n]+1080/);
  assert.match(source, /laptop-1366[^\n]+1366[^\n]+768/);
  assert.match(source, /mobile-390[^\n]+390[^\n]+844/);
  for (const marker of [
    "login-api",
    "today",
    "people",
    "governance",
    "archive-result",
    "clues",
    "map-8-locations",
    "contract-3-stages",
    "leadership-meeting",
    "forced-conversation-6",
    "morning-briefing",
    "save-load",
    "review",
    "main-endings-24",
  ]) {
    assert.match(source, new RegExp(`visual:${marker}`));
  }
  assert.match(source, /scrollWidth/);
  assert.match(source, /console\.json/);
  assert.match(source, /network\.json/);
  assert.match(source, /browser-summary\.json/);
});

test("Playwright config retains failure evidence without embedding credentials", () => {
  const config = read("../playwright.config.ts");
  const packageJson = JSON.parse(read("../package.json"));
  assert.match(config, /trace:\s*["']retain-on-failure["']/);
  assert.match(config, /screenshot:\s*["']only-on-failure["']/);
  assert.match(config, /video:\s*["']retain-on-failure["']/);
  assert.match(config, /127\.0\.0\.1:3001/);
  assert.equal(
    packageJson.scripts["test:e2e:full"],
    "playwright test e2e/full-game.spec.ts e2e/visual-matrix.spec.ts",
  );
  assert.equal(packageJson.devDependencies["@playwright/test"].startsWith("^"), false);
  assert.doesNotMatch(config, /api[_-]?key/i);
});

test("production server resolves its build output from the project script", () => {
  const packageJson = JSON.parse(read("../package.json"));
  const source = read("../scripts/start-production.mjs");
  assert.equal(packageJson.scripts.start, "node scripts/start-production.mjs");
  assert.match(source, /startProdServer/);
  assert.match(source, /fileURLToPath\(new URL\("\.\.\/dist"/);
});
