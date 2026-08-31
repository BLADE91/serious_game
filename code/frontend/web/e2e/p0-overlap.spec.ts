import { expect, test } from "@playwright/test";

const enabled = process.env.RUN_P0_OVERLAP_E2E === "1";

test.use({ trace: "on" });

test.describe("P0 forced-group and stale-governance overlap", () => {
  test.skip(!enabled, "set RUN_P0_OVERLAP_E2E=1 for the deterministic browser fixture");

  test("renders the forced group layer and submits only its group stream", async ({ page }) => {
    const group = {
      conversation_id: "group-overlap-fixture",
      phase: "active",
      agenda: "请当面说明安置清单与复核安排",
      turn_count: 0,
      participant_states: [{ npc_id: "npc_sun_qiang", status: "active" }],
    };
    const staleAction = {
      action_instance_id: "gov-overlap-fixture",
      action_kind: "household_visit",
      status: "active",
      title: "入户走访",
    };
    let groupTurns = 0;
    let governanceTurns = 0;

    await page.route("**/api/backend/api/game/session/*/view?**", async route => {
      const response = await route.fetch();
      const payload = await response.json();
      const state = payload.state || payload.visible_state || payload;
      const overlapping = {
        ...state,
        active_group_conversation: group,
        active_conversation: { conversation_id: "ordinary-overlap-fixture", npc_name: "普通会谈对象", turn_count: 1 },
        active_governance_action: staleAction,
      };
      await route.fulfill({ response, json: { ...payload, state: overlapping, visible_state: overlapping } });
    });
    await page.route("**/api/backend/api/game/session/*/governance", async route => {
      const response = await route.fetch();
      const payload = await response.json();
      await route.fulfill({ response, json: { ...payload, governance_actions: [staleAction], meetings: [] } });
    });
    await page.route("**/api/backend/api/game/session/*/group-conversation/turn/stream", async route => {
      groupTurns += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/x-ndjson",
        body: `${JSON.stringify({ type: "complete", result: { state_version: 2 } })}\n`,
      });
    });
    await page.route("**/api/backend/api/game/session/*/governance/**/turn/stream", async route => {
      governanceTurns += 1;
      await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ error: { code: "WRONG_ENDPOINT" } }) });
    });

    await page.goto("/");
    await page.getByRole("button", { name: /进入游戏/ }).first().click();
    await expect(page.getByRole("heading", { name: "进入云溪县" })).toBeVisible();
    await page.getByRole("button", { name: /开始新游戏/ }).click();

    await expect(page.locator('[data-primary-scene="forced_group_conversation"]')).toBeVisible();
    await expect(page.getByTestId("forced-group-conversation")).toBeVisible();
    await expect(page.getByTestId("active-group-conversation-compact")).toBeVisible();
    await expect(page.getByRole("button", { name: "中止行动" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "结束本次行动" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "签订合同" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "结束会谈" })).toHaveCount(0);

    await page.locator("form.conversation-bar textarea").fill("请将事实、责任人与复核节点逐项记录，并保留异议。 ");
    const groupResponse = page.waitForResponse(response => response.request().method() === "POST"
      && /\/group-conversation\/turn\/stream$/.test(new URL(response.url()).pathname));
    await page.getByRole("button", { name: "送出回应" }).click();
    expect((await groupResponse).ok()).toBe(true);
    await expect.poll(() => groupTurns).toBe(1);
    expect(governanceTurns).toBe(0);
  });
});
