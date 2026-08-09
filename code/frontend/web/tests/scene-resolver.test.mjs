import assert from "node:assert/strict";
import { readdir, stat } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { ENDING_SCENES, STORY_SCENES, blockIdFromContentInstance, resolveScene, resolveSceneForView } from "../app/lib/scene-resolver.ts";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("resolves identifiers in the required priority order and records matchedBy", () => {
  const resolved = resolveScene({
    contentInstanceId: "block:d01_arrival_drive",
    blockId: "d01_briefing_office",
    decisionId: "ev1_01_reception_bag",
    mainEndingId: "ending_24",
    beatId: "beat_d89_m2",
  });
  assert.equal(resolved.id, "C01_S01");
  assert.equal(resolved.matchedBy, "content_instance_id");
  assert.equal(resolved.matchedId, "d01_arrival_drive");

  assert.equal(resolveScene({ blockId: "d01_briefing_office", decisionId: "ev1_01_reception_bag" }).matchedBy, "block_id");
  assert.equal(resolveScene({ decisionId: "ev1_01_reception_bag", mainEndingId: "ending_24" }).matchedBy, "decision_id");
  assert.equal(resolveScene({ mainEndingId: "ending_24", beatId: "beat_d89_m2" }).matchedBy, "main_ending_id");
  assert.equal(resolveScene({ beatId: "beat_d89_m2" }).matchedBy, "beat_id");
  assert.equal(resolveScene({}).matchedBy, "fallback");
  assert.equal(blockIdFromContentInstance("block:d01_reception_scene"), "d01_reception_scene");
});

test("uses different camera shots for blocks inside the same beat", () => {
  const arrival = resolveScene({ contentInstanceId: "block:d01_arrival_drive", beatId: "beat_d01_arrival_and_reception" });
  const office = resolveScene({ contentInstanceId: "block:d01_briefing_office", beatId: "beat_d01_arrival_and_reception" });
  const reception = resolveScene({ contentInstanceId: "block:d01_reception_scene", beatId: "beat_d01_arrival_and_reception" });
  assert.deepEqual([arrival.id, office.id, reception.id], ["C01_S01", "C01_S02", "C01_S03"]);
  assert.equal(new Set([arrival.asset, office.asset, reception.asset]).size, 3);
});

test("keeps historical and current-state scene identifiers in separate time contexts", () => {
  const oldLine = { contentInstanceId: "block:d30_source_opening", storyDay: 30 };
  assert.equal(resolveSceneForView({ line: oldLine, currentIndex: 9, itemCount: 10, currentStoryDay: 31, decisionId: "dp3_01", beatId: "beat_d31_m2" }).id, "C03_S01");
  assert.equal(resolveSceneForView({ line: oldLine, currentIndex: 4, itemCount: 10, currentStoryDay: 31, decisionId: "dp3_01", beatId: "beat_d31_m2" }).id, "C02_S08");
  assert.equal(resolveSceneForView({ line: oldLine, currentIndex: 4, itemCount: 10, currentStoryDay: 90, mainEndingId: "ending_24", beatId: "beat_d90_m2" }).id, "C02_S08");
  assert.equal(resolveSceneForView({ line: { contentInstanceId: "block:d18_source_opening", storyDay: 18 }, currentIndex: 9, itemCount: 10, currentStoryDay: 19, beatId: "beat_d19_m2" }).id, "C02_S02");
  assert.equal(resolveSceneForView({ line: { contentInstanceId: "block:d90_source_opening", storyDay: 90 }, currentIndex: 9, itemCount: 10, currentStoryDay: 90, mainEndingId: "ending_24", beatId: "beat_d90_m2" }).id, "E24");
});

test("inherits the nearest same-day scene for dialogue without presentation identifiers", () => {
  const lines = [
    { contentInstanceId: "block:d01_reception_scene", storyDay: 1 },
    { storyDay: 1 },
  ];
  assert.equal(resolveSceneForView({ line: lines[1], lines, currentIndex: 1, itemCount: 2, currentStoryDay: 1, beatId: "beat_d01_arrival_and_reception" }).id, "C01_S03");
});

test("covers exactly 48 story scenes and all 24 real main endings", async () => {
  assert.equal(STORY_SCENES.length, 48);
  assert.equal(ENDING_SCENES.length, 24);
  assert.equal(new Set(STORY_SCENES.map(item => item.id)).size, 48);
  assert.equal(new Set(ENDING_SCENES.map(item => item.id)).size, 24);
  assert.equal(new Set(STORY_SCENES.flatMap(item => item.blockIds)).size, STORY_SCENES.reduce((total, item) => total + item.blockIds.length, 0));
  assert.equal(new Set(STORY_SCENES.flatMap(item => item.decisionIds)).size, STORY_SCENES.reduce((total, item) => total + item.decisionIds.length, 0));

  for (let index = 1; index <= 24; index += 1) {
    const endingId = `ending_${String(index).padStart(2, "0")}`;
    assert.equal(resolveScene({ mainEndingId: endingId }).id, `E${String(index).padStart(2, "0")}`);
  }

  const files = (await readdir(path.join(projectRoot, "public", "scenes"))).sort();
  const expected = [...STORY_SCENES, ...ENDING_SCENES].map(item => path.basename(item.asset)).sort();
  assert.deepEqual(files, expected);
  for (const file of files) assert.ok((await stat(path.join(projectRoot, "public", "scenes", file))).size > 0);
  assert.equal(files.some(file => /rejected|qa|backup|prompt|source|overview|script|备份|提示词|源图|总览|处理脚本|\.png$|\.jpg$/i.test(file)), false);
});
