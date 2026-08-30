import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { resourceInventoryView } from "../app/lib/player-ui.ts";

const shell = await readFile(new URL("../app/GameShell.tsx", import.meta.url), "utf8");

test("hides decision forecasts and exact thresholds while keeping discovered NPC demands", () => {
  assert.doesNotMatch(shell, /data-testid="decision-tradeoff-preview"/);
  assert.doesNotMatch(shell, /data-testid="threshold-alerts"/);
  assert.match(shell, /data-testid="npc-demand-card"/);
  assert.match(shell, /data-testid="resource-pool-summary"/);
  assert.doesNotMatch(shell, /tradeoff_preview/);
  assert.equal(resourceInventoryView([{
    resource_id: "hearing_slot",
    name: "公开听证场次",
    category: "administrative_capacity",
    capacity: 3,
    available_to_reserve: 2,
    blocked_total: 1,
  }])[0].available, 2);
});

test("wires every NPC demand disposition to the authoritative backend", () => {
  assert.match(shell, /governance\/npc-demands\/\$\{encodeURIComponent/);
  for (const transition of [
    "acknowledged", "committed", "satisfied", "lawfully_refused", "breached",
  ]) {
    assert.match(shell, new RegExp(transition));
  }
});
