import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const shellPath = new URL("../app/GameShell.tsx", import.meta.url);
const apiPath = new URL("../app/lib/api.ts", import.meta.url);

test("keeps every state-changing gameplay workflow connected to an API route", async () => {
  const [shell, api] = await Promise.all([
    readFile(shellPath, "utf8"),
    readFile(apiPath, "utf8"),
  ]);
  const source = `${shell}\n${api}`;
  for (const route of [
    "/action/stream",
    "/group-conversation/turn/stream",
    "/governance/actions",
    "/governance/meetings/",
    "/governance/documents/",
    "/governance/contract-batches/",
    "/governance/contracts/",
    "/actions/quote",
    "/end-day",
    "/manual-saves",
    "/load-snapshot",
  ]) {
    assert.match(source, new RegExp(route.replaceAll("/", "\\/")));
  }
});

test("surfaces forced group conversations and the complete contract lifecycle without private night traces", async () => {
  const shell = await readFile(shellPath, "utf8");
  assert.match(shell, /ForcedGroupConversationScene/);
  assert.match(shell, /发起人：/);
  assert.match(shell, /participant_ids/);
  assert.match(shell, /contract_batch_proposal/);
  assert.match(shell, /确认逐户合同提议/);
  assert.match(shell, /核验条款并生成合同/);
  assert.match(shell, /保存正文并重新审校/);
  assert.match(shell, /送交本户复核/);
  assert.match(shell, /正式签署并入账/);
  assert.match(shell, /group_conversation_timeline/);
  assert.doesNotMatch(shell, /contact_selections/);
  assert.doesNotMatch(shell, /contact_responses/);
  assert.doesNotMatch(shell, /agent_exchanges/);
});

test("surfaces the backend overtime mechanism when daily energy reaches zero", async () => {
  const shell = await readFile(shellPath, "utf8");
  assert.match(shell, /ledger\.action_points\.overtime_available/);
  assert.match(shell, /ledger\.action_points\.chapter_overtime_remaining/);
  assert.match(shell, /input_mode: "overtime"/);
  assert.match(shell, /parameters: \{ points \}/);
  assert.match(shell, /申请加班/);
  assert.match(shell, /fatigue\.label/);
  assert.match(shell, /新增精力会增加日终疲惫/);
  assert.doesNotMatch(shell, /active_rest:\s*true/);
});

test("surfaces required model consent before NPC gameplay", async () => {
  const [shell, api] = await Promise.all([
    readFile(shellPath, "utf8"),
    readFile(apiPath, "utf8"),
  ]);
  assert.match(api, /\/api\/consent\/current/);
  assert.match(api, /\/api\/consent"/);
  assert.match(api, /third_party_model/);
  assert.match(shell, /model_consent_required/);
  assert.match(shell, /同意必要授权并继续/);
  assert.match(shell, /撤回授权/);
});

test("uses in-game confirmation panels instead of browser-native blocking dialogs", async () => {
  const shell = await readFile(shellPath, "utf8");
  assert.doesNotMatch(shell, /window\.confirm/);
  assert.match(shell, /结束今日工作/);
  assert.match(shell, /进入夜间结算/);
  assert.match(shell, /载入关键节点/);
  assert.match(shell, /覆盖已有关键节点/);
});

test("lists all saves and makes version-locked incompatible progress explicit", async () => {
  const [shell, api] = await Promise.all([readFile(shellPath, "utf8"), readFile(apiPath, "utf8")]);
  assert.match(api, /\/api\/game\/sessions/);
  assert.match(shell, /saved\.loadable === false/);
  assert.match(shell, /unavailable_reason/);
});

test("does not report rejected governance input as an NPC success", async () => {
  const shell = await readFile(shellPath, "utf8");
  assert.match(shell, /result\.input_rejected/);
  assert.match(shell, /这句话没有送达/);
  assert.match(shell, /typeof success === "function"/);
});

test("renders every non-meeting NPC exchange as a Galgame stage", async () => {
  const shell = await readFile(shellPath, "utf8");
  assert.match(shell, /action\.action_kind !== "leadership_meeting"/);
  assert.match(shell, /className="gal-stage governance-gal-stage conversation-mode"/);
  assert.match(shell, /data-testid="governance-gal-scene"/);
  assert.match(shell, /className="gal-portrait" aria-label=\{`\$\{targetName\}立绘`\}/);
  assert.match(shell, /className="gal-stage forced-group-gal-stage conversation-mode"/);
  assert.match(shell, /streamingReplies=\{streamingReplies\}/);
});
