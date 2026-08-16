import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const shell = await readFile(new URL("../app/GameShell.tsx", import.meta.url), "utf8");

test("renders the ten-day progress broadcast with replayable audio", () => {
  assert.match(shell, /data-testid="progress-broadcast"/);
  assert.match(shell, /progress_broadcast/);
  assert.match(shell, /qingjiang-progress-broadcast:/);
  assert.match(shell, /AudioContext/);
  assert.match(shell, /重播督办提示音/);
  assert.match(shell, /values\(broadcast\.signals\)/);
  assert.match(shell, /第 \{progressBroadcast\.story_day\} 日督办/);
});
