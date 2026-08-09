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
    assert.equal(character.portraitPath, `/characters/${character.id}.webp`);
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

test("ships exactly the mapped WebP runtime assets", async () => {
  const files = (await readdir(assetRoot)).sort();
  const expected = CHARACTERS.map(character => `${character.id}.webp`).sort();
  assert.deepEqual(files, expected);

  for (const file of files) {
    assert.ok((await stat(path.join(assetRoot, file))).size > 0, `${file} should not be empty`);
  }
  assert.equal(files.some(file => /manifest|qa|source|源图|\.png$/i.test(file)), false);
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
