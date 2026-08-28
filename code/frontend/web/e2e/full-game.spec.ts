import { expect, test, type Page, type TestInfo } from "@playwright/test";
import { appendFile, mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

type JsonMap = Record<string, unknown>;
type RouteProfile = {
  route_id: string;
  origin_id: string;
  target_main_ending_ids: string[];
  target_sub_ending_ids: string[];
  decision_policy_template_id?: string;
  decision_policy: Record<string, unknown>;
  expected_end_day: number;
};

type RouteCatalog = {
  profiles: RouteProfile[];
  decision_policy_templates: Record<string, Record<string, unknown>>;
  main_ending_policy_overrides: Record<string, Record<string, unknown>>;
  sub_ending_policy_overrides: Record<string, Record<string, unknown>>;
};

type Decision = {
  decision_id: string;
  options: Array<{ option_id: string; text: string }>;
};

const contentRoot = path.resolve(process.cwd(), "../../backend/content/packages/pkg_gameplay_v3");
const routeCatalogPath = path.join(contentRoot, "acceptance_route_profiles.json");
const enabled = process.env.RUN_FULL_REAL_E2E === "1";
const shardIndex = Number(process.env.FULL_E2E_SHARD_INDEX || 0);
const shardTotal = Math.max(1, Number(process.env.FULL_E2E_SHARD_TOTAL || 1));
const browserEvidenceRoot = process.env.FULL_ACCEPTANCE_BROWSER_DIR
  ? path.resolve(process.env.FULL_ACCEPTANCE_BROWSER_DIR)
  : path.resolve(process.cwd(), "../../../output/full-acceptance/playwright");
const visualManifest = path.join(browserEvidenceRoot, "browser-state-manifest.jsonl");
const visualViewports = [
  { name: "desktop-1920", width: 1920, height: 1080 },
  { name: "laptop-1366", width: 1366, height: 768 },
  { name: "mobile-390", width: 390, height: 844 },
];

const readJson = async <T>(file: string): Promise<T> => JSON.parse(await readFile(file, "utf8")) as T;
const asMap = (value: unknown): JsonMap => value && typeof value === "object" && !Array.isArray(value) ? value as JsonMap : {};

async function recordVisualState(
  page: Page,
  testInfo: TestInfo,
  routeId: string,
  state: string,
  variant = "default",
  metadata: JsonMap = {},
) {
  const original = page.viewportSize();
  for (const viewport of visualViewports) {
    await page.setViewportSize({ width: viewport.width, height: viewport.height });
    const layout = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
      portraitTextOverlap: (() => {
        const portrait = document.querySelector(".gal-portrait")?.getBoundingClientRect();
        const text = document.querySelector(".gal-dialogue p")?.getBoundingClientRect();
        if (!portrait || !text) return 0;
        return Math.max(0, Math.min(portrait.right, text.right) - Math.max(portrait.left, text.left))
          * Math.max(0, Math.min(portrait.bottom, text.bottom) - Math.max(portrait.top, text.top));
      })(),
    }));
    expect(layout.scrollWidth, `${state} must not overflow at ${viewport.name}`).toBeLessThanOrEqual(layout.clientWidth + 1);
    expect(layout.portraitTextOverlap, `${state} portrait must not cover dialogue at ${viewport.name}`).toBe(0);
    const safeName = `${state}-${variant}-${viewport.name}`.replace(/[^a-zA-Z0-9_-]+/g, "-");
    const screenshot = testInfo.outputPath("visual", `${safeName}.png`);
    await mkdir(path.dirname(screenshot), { recursive: true });
    await page.screenshot({ path: screenshot, fullPage: true });
    await mkdir(browserEvidenceRoot, { recursive: true });
    await appendFile(visualManifest, `${JSON.stringify({
      route_id: routeId, state, variant, viewport: viewport.name, width: viewport.width,
      height: viewport.height, screenshot, layout, ...metadata,
    })}\n`, "utf8");
  }
  if (original) await page.setViewportSize(original);
}

function mergedPolicy(profile: RouteProfile, catalog: RouteCatalog) {
  return Object.assign(
    {},
    profile.decision_policy_template_id ? catalog.decision_policy_templates[profile.decision_policy_template_id] : {},
    profile.decision_policy,
    ...profile.target_main_ending_ids.map(id => catalog.main_ending_policy_overrides[id] || {}),
    ...profile.target_sub_ending_ids.map(id => catalog.sub_ending_policy_overrides[id] || {}),
  );
}

async function installEvidenceObservers(page: Page, testInfo: TestInfo) {
  const consoleEvents: JsonMap[] = [];
  const networkEvents: JsonMap[] = [];
  page.on("console", message => {
    if (message.type() === "error" || message.type() === "warning") {
      consoleEvents.push({ type: message.type(), text: message.text(), location: message.location() });
    }
  });
  page.on("pageerror", error => consoleEvents.push({ type: "pageerror", text: error.message }));
  page.on("requestfailed", request => networkEvents.push({
    method: request.method(), url: new URL(request.url()).pathname,
    failure: request.failure()?.errorText || "request failed",
  }));
  page.on("response", response => {
    if (response.status() >= 400) networkEvents.push({
      method: response.request().method(), url: new URL(response.url()).pathname, status: response.status(),
    });
  });
  return async (summary: JsonMap) => {
    const folder = testInfo.outputPath("browser-evidence");
    await mkdir(folder, { recursive: true });
    await writeFile(path.join(folder, "console.json"), JSON.stringify(consoleEvents, null, 2));
    await writeFile(path.join(folder, "network.json"), JSON.stringify(networkEvents, null, 2));
    await writeFile(path.join(folder, "browser-summary.json"), JSON.stringify(summary, null, 2));
    expect(consoleEvents.filter(item => item.type === "error" || item.type === "pageerror"), "unattributed browser errors").toEqual([]);
    expect(networkEvents.filter(item => Number(item.status || 0) >= 500), "server errors observed by browser").toEqual([]);
  };
}

async function configureAndStart(page: Page, testInfo: TestInfo, routeId: string, routeIndex: number, originId: string) {
  await page.goto("/");
  await page.getByRole("button", { name: /登录|进入游戏/ }).first().click(); // acceptance:login
  const username = `e2e_${routeIndex}_${Date.now().toString(36)}`.slice(0, 32);
  const password = `E2e-${Date.now().toString(36)}-Safe!`;
  const register = page.getByRole("button", { name: "注册", exact: true });
  if (await register.isVisible().catch(() => false)) {
    await register.click();
    await page.getByLabel("用户名").fill(username);
    await page.getByLabel("密码").fill(password);
    await page.getByRole("button", { name: "注册并开始" }).click();
  } else if (await page.getByLabel("用户名").isVisible().catch(() => false)) {
    const configuredUser = process.env.FULL_E2E_USERNAME || "";
    const configuredPassword = process.env.FULL_E2E_PASSWORD || "";
    expect(configuredUser && configuredPassword, "registration is disabled; browser credentials are required").toBeTruthy();
    await page.getByLabel("用户名").fill(configuredUser);
    await page.getByLabel("密码").fill(configuredPassword);
    await page.getByRole("button", { name: "登录并继续" }).click();
  }

  // acceptance:api-configuration
  await expect(page.getByRole("heading", { name: "配置 AI 接口" })).toBeVisible();
  await recordVisualState(page, testInfo, routeId, "login-api");
  const serverDefault = page.getByLabel("使用服务器默认接口");
  await expect(serverDefault, "full acceptance requires a configured real server gateway").toBeEnabled();
  await serverDefault.check();
  await page.getByRole("button", { name: "启用服务器默认接口" }).click();
  await expect(page.getByText("接口测试成功", { exact: true })).toBeVisible({ timeout: 600_000 });
  await page.getByRole("button", { name: "配置成功，继续" }).click();
  await expect(page.getByRole("heading", { name: "进入清江县" })).toBeVisible();

  const sessionResponse = page.waitForResponse(response =>
    response.request().method() === "POST" && /\/api\/game\/session$/.test(new URL(response.url()).pathname),
  );
  await page.getByRole("button", { name: /开始新游戏/ }).click();
  const created = asMap(await (await sessionResponse).json());
  const state = asMap(created.visible_state || created.state || created);
  const sessionId = String(created.session_id || state.session_id || "");
  expect(sessionId).not.toBe("");

  const originButton = page.getByRole("button", { name: new RegExp(originId, "i") });
  if (await originButton.isVisible().catch(() => false)) await originButton.click();
  await recordVisualState(page, testInfo, routeId, "today", "day-1", { story_day: 1 });
  return { sessionId, username };
}

async function readPlayerState(page: Page, sessionId: string) {
  return page.evaluate(async id => {
    const response = await fetch(`/api/game/session/${encodeURIComponent(id)}`, { credentials: "include" });
    if (!response.ok) throw new Error(`player-safe state read failed: ${response.status}`);
    return response.json();
  }, sessionId) as Promise<JsonMap>;
}

function visibleState(payload: JsonMap) {
  return asMap(payload.visible_state || payload.state || payload);
}

async function revealCurrentDecision(page: Page) {
  for (let count = 0; count < 160; count += 1) {
    if (await page.locator(".decision-stage-inline").isVisible().catch(() => false)) return;
    const next = page.getByRole("button", { name: "下一段", exact: true });
    if (await next.isEnabled().catch(() => false)) await next.click();
    else break;
  }
}

async function resolveDecision(page: Page, pending: JsonMap, policy: Record<string, unknown>, decisions: Map<string, Decision>) {
  await revealCurrentDecision(page); // acceptance:narrative
  const decisionId = String(pending.decision_id || "");
  const configured = policy[decisionId];
  const inputKind = String(pending.input_kind || "");
  if (inputKind === "sorting") {
    await page.getByRole("button", { name: "确认优先顺序" }).click();
    return;
  }
  if (inputKind === "allocation") {
    const submit = page.getByRole("button", { name: "确认分配" });
    await expect(submit).toBeEnabled();
    await submit.click();
    return;
  }
  const definition = decisions.get(decisionId);
  const desiredId = typeof configured === "string" ? configured : String(asMap(configured).option_id || "");
  const desired = definition?.options.find(option => option.option_id === desiredId)
    || definition?.options.find(option => option.option_id.startsWith(`${desiredId}_`))
    || definition?.options[0];
  expect(desired, `decision ${decisionId} has no selectable catalog option`).toBeTruthy();
  const button = page.locator(".decision-option button").filter({ hasText: desired!.text }).first();
  await expect(button, `configured choice ${desired!.option_id} must be player-visible`).toBeEnabled();
  await button.click();
}

async function finishForcedConversation(page: Page, testInfo: TestInfo, routeId: string, planId: string) {
  // acceptance:night
  await recordVisualState(page, testInfo, routeId, "forced-conversation", planId, { plan_id: planId });
  let priorHistory = 0;
  for (let turn = 0; turn < 40; turn += 1) {
    const finish = page.getByRole("button", { name: "结束夜间会谈" });
    if (await finish.isVisible().catch(() => false)) {
      await finish.click();
      return;
    }
    const history = await page.locator('[aria-label="夜间会谈完整记录"] article').count();
    expect(history).toBeGreaterThanOrEqual(priorHistory);
    priorHistory = history;
    const input = page.locator("form.conversation-bar textarea");
    await expect(input).toBeVisible();
    await input.fill("我会把责任、依据和时间节点逐项公开说明；请指出你仍不放心的具体一项，我当场回应。不要接受空泛承诺，以后续可核对记录为准。");
    await page.getByRole("button", { name: "送出回应" }).click();
    await expect(page.getByRole("status").filter({ hasText: "正在" })).toBeVisible({ timeout: 15_000 }).catch(() => undefined);
    await expect(page.locator("form.conversation-bar textarea")).toBeEnabled({ timeout: 600_000 });
  }
  throw new Error("forced conversation did not resolve after 40 real-model turns");
}

async function finishOpenInteraction(page: Page) {
  const input = page.locator("form.conversation-bar textarea");
  if (await input.isVisible().catch(() => false)) {
    await input.fill("请围绕当前事项说明你掌握的事实、主要担忧和可以依法推进的下一步。 ");
    await page.getByRole("button", { name: "送出回应" }).click();
    await expect(input).toBeEnabled({ timeout: 600_000 });
  }
  const end = page.getByRole("button", { name: /结束本次行动|正式结束会谈/ }).first();
  if (await end.isVisible().catch(() => false)) await end.click();
}

async function satisfyMandatoryOpportunity(page: Page) {
  await page.getByRole("button", { name: /人物/ }).first().click(); // acceptance:people
  const start = page.getByRole("button", { name: "进入会谈" }).first();
  await expect(start, "mandatory opportunity must expose a player-visible entry").toBeEnabled();
  await start.click();
  const confirm = page.getByRole("button", { name: /发起行动|确认办理/ }).first();
  if (await confirm.isVisible().catch(() => false)) await confirm.click();
  await finishOpenInteraction(page);
}

async function endCurrentDay(page: Page, testInfo: TestInfo, routeId: string, day: number) {
  const end = page.getByRole("button", { name: "结束今日", exact: true }).last();
  await expect(end).toBeEnabled();
  await end.click();
  const confirm = page.getByRole("button", { name: /确认结束|结束今日/ }).last();
  if (await confirm.isVisible().catch(() => false)) await confirm.click();
  await expect(page.getByRole("status").filter({ hasText: /夜间结算|正在/ })).toBeVisible({ timeout: 15_000 }).catch(() => undefined);
  await expect(end).toBeEnabled({ timeout: 600_000 }).catch(() => undefined);
  const morning = page.getByText(/晨间|次晨|简报/).first();
  if (await morning.isVisible().catch(() => false)) {
    await recordVisualState(page, testInfo, routeId, "morning-briefing", `after-day-${day}`, { story_day: day + 1 });
  }
}

async function exercisePlayerPanels(page: Page, testInfo: TestInfo, routeId: string) {
  await page.getByRole("button", { name: /人物/ }).first().click(); // acceptance:people
  await expect(page.locator(".people-relationship-view")).toBeVisible();
  await recordVisualState(page, testInfo, routeId, "people");
  await page.getByRole("button", { name: /治理/ }).first().click(); // acceptance:governance acceptance:contract acceptance:meeting
  await expect(page.locator(".governance-panel")).toBeVisible();
  await recordVisualState(page, testInfo, routeId, "governance");
  const contractStages = new Set(await page.locator(".governance-panel").getAttribute("data-contract-stages").then(value => value?.split(",") || []).catch(() => []));
  if (contractStages.size === 4) await recordVisualState(page, testInfo, routeId, "contract-4-stages", "overview", { stage_count: 4 });
  await page.getByRole("button", { name: /线索/ }).first().click(); // acceptance:clues
  await expect(page.locator(".knowledge-panel")).toBeVisible();
  await recordVisualState(page, testInfo, routeId, "clues");
  await page.getByRole("button", { name: /地图/ }).first().click(); // acceptance:map
  await expect(page.locator(".map-panel")).toBeVisible();
  await recordVisualState(page, testInfo, routeId, "map-current", "day-1", {
    location_count: await page.locator(".location-list > article").count(),
  });
  await page.getByRole("button", { name: /关键节点/ }).first().click(); // acceptance:save-load
  await expect(page.getByText("五个关键节点")).toBeVisible();
  await recordVisualState(page, testInfo, routeId, "save-load");
  await page.getByRole("button", { name: /行动/ }).first().click(); // acceptance:archives
}

async function captureCompletedPanels(page: Page, testInfo: TestInfo, routeId: string) {
  await page.getByRole("button", { name: /地图/ }).first().click();
  await expect(page.locator(".map-panel")).toBeVisible();
  const locationCount = await page.locator(".location-list > article").count();
  expect(locationCount, "completed route must expose all eight map locations").toBe(8);
  await recordVisualState(page, testInfo, routeId, "map-8-locations", "all", { location_count: locationCount });
  await page.getByRole("button", { name: /复盘/ }).first().click();
  await expect(page.locator(".review-panel")).toBeVisible();
  await recordVisualState(page, testInfo, routeId, "review");
}

async function playRoute(page: Page, testInfo: TestInfo, sessionId: string, profile: RouteProfile, policy: Record<string, unknown>, decisions: Map<string, Decision>) {
  const visited = new Set<number>();
  let guard = 0;
  while (guard < 900) {
    guard += 1;
    const payload = await readPlayerState(page, sessionId);
    const state = visibleState(payload);
    const story = asMap(state.story);
    const day = Number(story.day || state.story_day || 0);
    if (day) visited.add(day);
    if (String(state.status) === "ended" || state.ending_result) break;
    if (state.active_group_conversation) {
      const group = asMap(state.active_group_conversation);
      await finishForcedConversation(page, testInfo, profile.route_id, String(group.followup_plan_id || group.plan_id || group.conversation_id || "unknown"));
      continue;
    }
    if (state.active_conversation || state.active_governance_action) {
      await finishOpenInteraction(page);
      continue;
    }
    const pending = asMap(state.pending_decision);
    if (pending.decision_id) {
      await resolveDecision(page, pending, policy, decisions);
      continue;
    }
    const commands = asMap(state.commands);
    if (commands.mandatory_opportunity_id || commands.can_end_day === false) {
      await satisfyMandatoryOpportunity(page);
      continue;
    }
    if (commands.can_end_day !== false) {
      await endCurrentDay(page, testInfo, profile.route_id, day);
      continue;
    }
    throw new Error(`route ${profile.route_id} has no legal player-visible progression at day ${day}`);
  }
  expect(guard, "route loop must terminate").toBeLessThan(900);
  const finalPayload = await readPlayerState(page, sessionId);
  const finalState = visibleState(finalPayload);
  expect(Number(asMap(finalState.story).day || finalState.story_day)).toBe(profile.expected_end_day);
  expect([...visited]).toEqual(Array.from({ length: 90 }, (_, index) => index + 1));
  return finalState;
}

const catalog = await readJson<RouteCatalog>(routeCatalogPath);
const decisionDocument = await readJson<{ decisions: Decision[] }>(path.join(contentRoot, "decisions.json"));
const decisions = new Map(decisionDocument.decisions.map(item => [item.decision_id, item]));
const selectedProfiles = catalog.profiles.filter((_, index) => index % shardTotal === shardIndex);

test.describe("full real browser routes", () => {
  test.skip(!enabled, "set RUN_FULL_REAL_E2E=1 only from the final real acceptance entrypoint");
  for (const [index, profile] of selectedProfiles.entries()) {
    test(`${profile.route_id} reaches its witnessed ending through player controls`, async ({ page }, testInfo) => {
      const finishEvidence = await installEvidenceObservers(page, testInfo);
      const { sessionId, username } = await configureAndStart(page, testInfo, profile.route_id, index, profile.origin_id);
      if (index === 0) await exercisePlayerPanels(page, testInfo, profile.route_id);
      const finalState = await playRoute(page, testInfo, sessionId, profile, mergedPolicy(profile, catalog), decisions);
      if (index === 0) await captureCompletedPanels(page, testInfo, profile.route_id);
      else await page.getByRole("button", { name: /复盘/ }).first().click(); // acceptance:review acceptance:ending
      const ending = page.getByLabel("最终结局");
      await expect(ending).toBeVisible();
      await recordVisualState(page, testInfo, profile.route_id, "main-ending", profile.target_main_ending_ids[0], {
        main_ending_ids: profile.target_main_ending_ids,
        sub_ending_ids: profile.target_sub_ending_ids,
      });
      for (const endingId of profile.target_main_ending_ids) expect(JSON.stringify(finalState)).toContain(endingId);
      for (const endingId of profile.target_sub_ending_ids) expect(JSON.stringify(finalState)).toContain(endingId);
      await finishEvidence({ route_id: profile.route_id, session_id: sessionId, username, story_day: 90, status: "passed" });
    });
  }
});
