import assert from "node:assert/strict";
import { readdir, readFile, stat } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { CHARACTERS, resolveCharacter } from "../app/lib/characters.ts";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const assetRoot = path.join(projectRoot, "public", "characters");

test("publishes one unique public mapping for all 30 characters", () => {
  assert.equal(CHARACTERS.length, 30);
  assert.equal(new Set(CHARACTERS.map(character => character.id)).size, 30);
  assert.equal(new Set(CHARACTERS.map(character => character.name)).size, 30);

  for (const character of CHARACTERS) {
    assert.deepEqual(Object.keys(character).sort(), ["aliases", "id", "name", "portraitPath", "role"]);
    assert.match(character.id, /^(npc|player)_[a-z_]+$/);
    assert.equal(character.portraitPath, `/characters/${character.id}.png`);
    assert.ok(character.name);
    assert.ok(character.role);
  }
});

test("resolves stable IDs, formal names, aliases, and fallback candidates", () => {
  assert.equal(resolveCharacter("npc_ning_dehai")?.name, "宁德海");
  assert.equal(resolveCharacter("宁德海")?.id, "npc_ning_dehai");
  assert.equal(resolveCharacter("宁老")?.id, "npc_ning_dehai");
  assert.equal(resolveCharacter("李县长")?.id, "player_li_zhiyuan");
  assert.equal(resolveCharacter(" NPC_NING_DEHAI ")?.name, "宁德海");
  assert.equal(resolveCharacter("unknown", "npc_wu_xiuying")?.name, "吴秀英");
  assert.equal(resolveCharacter("unknown"), null);
  assert.equal(resolveCharacter(null, undefined, ""), null);
});

test("ships every mapped original PNG runtime asset", async () => {
  const files = (await readdir(assetRoot)).sort();
  const expected = CHARACTERS.map(character => `${character.id}.png`).sort();
  const pngFiles = files.filter(file => file.endsWith(".png"));
  assert.deepEqual(pngFiles, expected);

  for (const file of pngFiles) {
    assert.ok((await stat(path.join(assetRoot, file))).size > 0, `${file} should not be empty`);
  }
  assert.equal(files.some(file => /manifest|qa|source|源图/i.test(file)), false);
});

test("keeps portrait rendering in the requested UI surfaces with a text fallback", async () => {
  const source = await readFile(path.join(projectRoot, "app", "GameShell.tsx"), "utf8");
  assert.match(source, /function CharacterPortrait/);
  assert.match(source, /onError=\{\(\) => setFailedPath\(character\.portraitPath\)\}/);
  assert.match(source, /"active-conversation-character"/);
  assert.match(source, /conversation\.turn_count \|\| conversation\.turns_completed/);
  assert.match(source, /data-testid="player-identity-card"/);
  assert.match(source, /resolveCharacter\(item\.npc_id, item\.target_npc_id, item\.npc_name\)/);
  assert.match(source, /resolveCharacter\(state\.active_conversation\.npc_id, state\.active_conversation\.target_npc_id, state\.active_conversation\.npc_name\)/);
  assert.match(source, /className="avatar"/);
});

test("adds profile navigation to every person-selection surface", async () => {
  const source = await readFile(path.join(projectRoot, "app", "GameShell.tsx"), "utf8");
  assert.match(source, /function CharacterChoiceCard/);
  assert.match(source, /aria-label=\{`查看\$\{displayName\}人物介绍`\}/);
  assert.match(source, /<CharacterProfileView character=\{characterProfileOpen\}/);
  assert.match(source, /character-choice-grid/);
  assert.match(source, /onOpenProfile=\{setCharacterProfileOpen\}/);
  assert.match(source, /className="person-portrait profile-avatar-button"/);
  assert.match(source, /人物介绍不会提前泄露隐藏剧情/);
});
