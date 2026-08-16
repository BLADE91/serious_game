import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const shell = await readFile(new URL("../app/GameShell.tsx", import.meta.url), "utf8");

test("hides decision forecasts and exact thresholds while keeping discovered NPC demands", () => {
  assert.doesNotMatch(shell, /data-testid="decision-tradeoff-preview"/);
  assert.doesNotMatch(shell, /data-testid="threshold-alerts"/);
  assert.match(shell, /data-testid="npc-demand-card"/);
  assert.match(shell, /data-testid="resource-pool-summary"/);
  assert.doesNotMatch(shell, /tradeoff_preview/);
  assert.match(shell, /available_to_reserve/);
});

test("wires every NPC demand disposition to the authoritative backend", () => {
  assert.match(shell, /governance\/npc-demands\/\$\{encodeURIComponent/);
  for (const transition of [
    "acknowledged", "committed", "satisfied", "lawfully_refused", "breached",
  ]) {
    assert.match(shell, new RegExp(transition));
  }
});
