import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { actionPointCost, actionPointLabel, toPlayerText } from "../app/lib/player-ui.ts";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("normalizes every supported action point cost shape including zero", () => {
  assert.equal(actionPointCost({ cost_action_points: 2 }), 2);
  assert.equal(actionPointCost({ action_point_cost: 3 }), 3);
  assert.equal(actionPointCost({ cost: { action_points: 4 } }), 4);
  assert.equal(actionPointCost({ cost: 5 }), 5);
  assert.equal(actionPointCost({ ap_cost: "6" }), 6);
  assert.equal(actionPointCost({ cost_action_points: 0, action_point_cost: 9 }), 0);
  assert.equal(actionPointCost({}), null);
  assert.equal(actionPointLabel({ cost_action_points: 0 }), "不消耗精力");
  assert.equal(actionPointLabel({ cost_action_points: 2 }), "消耗 2 点精力");
});

test("sanitizes player copy", async () => {
  assert.equal(toPlayerText("[BEAT_C01] NPC与玩家对应剧情节点"), "人物与你后续事态");
});

test("keeps the visible conversation loop and removes the old terminal surface", async () => {
  const source = await readFile(path.join(projectRoot, "app", "GameShell.tsx"), "utf8");
  assert.match(source, />结束会谈</);
  assert.match(source, /input_mode: "conversation_end"/);
  assert.match(source, /"active-conversation-character"/);
  assert.match(source, /data-testid="active-conversation-compact"/);
  assert.match(source, /compactCharacter\?\.role/);
  assert.match(source, /actionPointLabel\(item\)/);
  assert.match(source, /api\.loadSnapshot[\s\S]*"已载入所选存档", true/);
  assert.doesNotMatch(source, />WORKSPACE</);
  assert.doesNotMatch(source, /state v\{/);
  assert.doesNotMatch(source, /\[ERR\]/);
  assert.doesNotMatch(source, /placeholder=.*输入命令/);
  assert.doesNotMatch(source, /aria-label="终端命令"/);
  assert.doesNotMatch(source, /item\.glyph/);
  assert.match(source, /卷宗 \{chineseIndex\(index\)\}/);
  const images = source.split("\n").filter(line => line.includes("<Image"));
  assert.equal(images.length, 2);
  for (const image of images) {
    assert.match(image, /\bunoptimized\b/);
    assert.match(image, /\balt=/);
    assert.match(image, /\bsizes=/);
  }
  const portraitImage = images.find(line => line.includes('className="character-portrait"'));
  const sceneImage = images.find(line => line.includes('className="scene-backdrop"'));
  assert.match(portraitImage || "", /style=\{\{ objectFit: "contain", objectPosition: "center bottom" \}\}/);
  assert.doesNotMatch(sceneImage || "", /objectFit: "contain"/);
});

test("reads the private backend address from the Cloudflare runtime binding", async () => {
  const source = await readFile(path.join(projectRoot, "app", "api", "backend", "[...path]", "route.ts"), "utf8");
  assert.match(source, /await import\("cloudflare:workers"\)/);
  assert.match(source, /runtimeUrl \|\| process\.env\.GAME_BACKEND_URL \|\| "http:\/\/127\.0\.0\.1:8100"/);
  assert.doesNotMatch(source, /NEXT_PUBLIC_GAME_BACKEND_URL/);
});

test("keeps narrow-screen auxiliary controls touch friendly", async () => {
  const source = await readFile(path.join(projectRoot, "app", "GameShell.tsx"), "utf8");
  const styles = await readFile(path.join(projectRoot, "app", "globals.css"), "utf8");
  assert.match(source, /className="refresh-button"/);
  assert.match(styles, /\.refresh-button, \.history-toggle, \.icon-button \{ min-height: 44px; \}/);
  assert.match(styles, /\.refresh-button, \.icon-button \{ min-width: 44px; \}/);
});

test("uses the warm archival palette without the former green shell", async () => {
  const styles = await readFile(path.join(projectRoot, "app", "globals.css"), "utf8");
  const retiredGreenTokens = [
    "#10130f", "#161a17", "#20251f", "#2b3028", "#23261f",
    "#242821", "#1a1e19", "#34362d", "#292d27", "#171b17",
    "#607c50", "#8fac70", "--green",
  ];
  for (const token of retiredGreenTokens) assert.doesNotMatch(styles, new RegExp(token));
  assert.match(styles, /--charcoal: #1c1812/);
  assert.match(styles, /\.metric-strip \{[\s\S]*?background: #2a231a/);
  assert.match(styles, /\.context-panel \{[\s\S]*?background: var\(--paper-deep\)/);
  assert.match(styles, /\.context-panel \{[\s\S]*?background: #d8c7a4/);
  assert.match(styles, /\.panel-body \{ background: #d8c7a4/);
});
