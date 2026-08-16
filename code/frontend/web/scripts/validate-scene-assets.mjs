import assert from "node:assert/strict";
import { readFile, readdir, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { ENDING_SCENES, STORY_SCENES } from "../app/lib/scene-resolver.ts";
import { ENDING_ASSET_COUNT, SCENE_ASSET_WHITELIST, STORY_ASSET_COUNT } from "./scene-asset-whitelist.mjs";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sourceRoot = path.resolve(projectRoot, "..", "photo", "全本场景图");
const outputRoot = path.resolve(projectRoot, "public", "scenes");
const backendPackage = path.resolve(projectRoot, "..", "..", "backend", "content", "packages", "pkg_gameplay_v2");
const forbidden = /rejected|qa|backup|prompt|source|overview|script|备份|提示词|源图|总览|处理脚本|\.png$|\.jpg$/i;
const MAX_SCENE_BYTES = 600 * 1024;
const MAX_SCENE_TOTAL_BYTES = 44 * 1024 * 1024;

assert.equal(STORY_ASSET_COUNT, 48);
assert.equal(ENDING_ASSET_COUNT, 24);
assert.equal(SCENE_ASSET_WHITELIST.length, 72);
assert.equal(new Set(SCENE_ASSET_WHITELIST.map(item => item.source)).size, 72);
assert.equal(new Set(SCENE_ASSET_WHITELIST.map(item => item.destination)).size, 72);
assert.equal(STORY_SCENES.length, 48);
assert.equal(ENDING_SCENES.length, 24);

const expected = SCENE_ASSET_WHITELIST.map(item => item.destination).sort();
const publicEntries = await readdir(outputRoot, { withFileTypes: true });
assert.ok(publicEntries.every(entry => entry.isFile()), "public/scenes must be flat");
assert.deepEqual(publicEntries.map(entry => entry.name).sort(), expected, "public/scenes must contain only approved assets");

let sceneTotalBytes = 0;
for (const item of SCENE_ASSET_WHITELIST) {
  assert.doesNotMatch(item.destination, forbidden);
  assert.ok((await stat(path.resolve(sourceRoot, item.source))).size > 0, `missing source ${item.source}`);
  const publicAsset = await stat(path.resolve(outputRoot, item.destination));
  assert.ok(publicAsset.size > 0, `empty public asset ${item.destination}`);
  assert.ok(publicAsset.size <= MAX_SCENE_BYTES, `${item.destination} exceeds the 600 KiB runtime budget`);
  sceneTotalBytes += publicAsset.size;
}
assert.ok(sceneTotalBytes <= MAX_SCENE_TOTAL_BYTES, "scene assets exceed the 44 MiB runtime budget");

const resolverAssets = [...STORY_SCENES, ...ENDING_SCENES].map(item => path.basename(item.asset)).sort();
assert.deepEqual(resolverAssets, expected, "resolver and public whitelist must cover the same assets");

const beats = JSON.parse(await readFile(path.join(backendPackage, "story_beats.json"), "utf8")).beats;
const decisions = JSON.parse(await readFile(path.join(backendPackage, "decisions.json"), "utf8")).decisions;
const endings = JSON.parse(await readFile(path.join(backendPackage, "ending_rules.json"), "utf8")).main_endings;
const beatIds = new Set(beats.map(item => item.beat_id));
const blockIds = new Set(beats.flatMap(item => [...(item.opening_blocks || []), ...(item.night_blocks || [])]).map(item => item.block_id));
const decisionIds = new Set(decisions.map(item => item.decision_id));
const endingIds = new Set(endings.map(item => item.ending_id));
const storySceneIds = new Set(STORY_SCENES.map(item => item.id));

for (const scene of STORY_SCENES) {
  assert.ok(scene.beatIds.length || scene.blockIds.length || scene.decisionIds.length, `${scene.id} has no auditable mapping`);
}
for (const beat of beats) {
  for (const block of [...(beat.opening_blocks || []), ...(beat.night_blocks || [])]) {
    assert.ok(storySceneIds.has(block.scene_id), `${block.block_id} has unknown or missing scene_id`);
  }
}
for (const decision of decisions) {
  assert.ok(storySceneIds.has(decision.scene_id), `${decision.decision_id} has unknown or missing scene_id`);
  for (const block of [...(decision.presentation_blocks || []), ...(decision.followup_blocks || [])]) {
    assert.ok(storySceneIds.has(block.scene_id), `${block.block_id} has unknown or missing scene_id`);
  }
}
const mappedDecisionIds = new Set(STORY_SCENES.flatMap(scene => scene.decisionIds));
assert.deepEqual([...mappedDecisionIds].sort(), [...decisionIds].sort(), "all real decisions must have an auditable scene fallback");
assert.equal(mappedDecisionIds.size, STORY_SCENES.reduce((total, scene) => total + scene.decisionIds.length, 0), "decision IDs must not map to multiple scenes");
assert.equal(new Set(STORY_SCENES.flatMap(scene => scene.blockIds)).size, STORY_SCENES.reduce((total, scene) => total + scene.blockIds.length, 0), "block IDs must not map to multiple scenes");
for (let index = 1; index <= 24; index += 1) {
  assert.ok(endingIds.has(`ending_${String(index).padStart(2, "0")}`));
}

console.log("Scene assets validated: 48 story scenes, 24 endings, no public leakage.");
