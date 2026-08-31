import { expect, test } from "@playwright/test";

test("clicking an earlier NPC timeline entry switches the rendered speaker without a page error", async ({ page }) => {
  const pageErrors: Error[] = [];
  page.on("pageerror", error => pageErrors.push(error));

  const groupConversation = {
    conversation_id: "component-group-1",
    phase: "active",
    agenda: "核对安置与复核安排",
    initiator_npc_id: "npc_yuan_guilan",
    participant_ids: ["npc_sun_qiang", "npc_yuan_guilan"],
    participant_states: [
      { npc_id: "npc_sun_qiang", status: "active", public_summary: "要求明确责任" },
      { npc_id: "npc_yuan_guilan", status: "active", public_summary: "要求核对安置" },
    ],
    transcript: [
      { speaker_type: "npc", npc_id: "npc_sun_qiang", npc_name: "孙强", text: "请先明确责任人与复核节点。" },
      { speaker_type: "npc", npc_id: "npc_yuan_guilan", npc_name: "袁桂兰", text: "我的安置清单还需要逐项核对。" },
    ],
  };
  const state = {
    session_id: "component-session",
    status: "active",
    state_version: 1,
    story: { day: 2 },
    ledger: { action_points: { available: 3, daily_cap: 3 }, budget: { available: 8000 } },
    active_group_conversation: groupConversation,
  };

  await page.route("**/api/backend/**", async route => {
    const url = new URL(route.request().url());
    const backendPath = url.pathname.replace(/^\/api\/backend/, "");
    let body: Record<string, unknown> = {};
    if (backendPath === "/health/ready") body = { authentication_required: false, model_consent_required: false };
    else if (backendPath === "/api/ai/config") body = { active: true, mode: "personal", endpoint: "https://fixture.invalid/v1", model: "fixture-model" };
    else if (backendPath === "/api/game/session" && route.request().method() === "POST") body = { session_id: "component-session" };
    else if (backendPath.includes("/api/game/session/component-session/view")) body = {
      state,
      visible_state: state,
      commands: {},
      feed: { items: [{ id: "fixture-line", story_day: 2, text: "会谈已经开始。" }], cursor: 1 },
    };
    else if (backendPath.endsWith("/governance")) body = { governance_actions: [], meetings: [] };
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });

  await page.goto("/");
  await expect(page.getByText("游戏已就绪", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "进入游戏", exact: true }).click();
  expect(pageErrors).toEqual([]);
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: /开始新游戏/ }).click();

  const speaker = page.locator(".forced-group-speaker");
  await expect(speaker).toHaveAttribute("aria-label", "袁桂兰立绘");
  await expect(speaker.getByText("袁桂兰", { exact: true })).toBeVisible();
  await expect(speaker.getByText("困难户", { exact: true })).toBeVisible();
  await expect(page.getByText("我的安置清单还需要逐项核对。", { exact: true })).toBeVisible();

  await page.locator(".forced-group-timeline button.npc").filter({ hasText: "孙强" }).click();

  await expect(speaker).toHaveAttribute("aria-label", "孙强立绘");
  await expect(speaker.getByText("孙强", { exact: true })).toBeVisible();
  await expect(speaker.getByText("渡口镇党委书记", { exact: true })).toBeVisible();
  await expect(page.getByText("请先明确责任人与复核节点。", { exact: true })).toBeVisible();
  expect(pageErrors.map(error => error.message)).not.toContainEqual(expect.stringContaining("setSelectedNpcId"));
  expect(pageErrors).toEqual([]);
});
