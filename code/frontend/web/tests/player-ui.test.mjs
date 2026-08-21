import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { actionPointCost, actionPointLabel, toPlayerText } from "../app/lib/player-ui.ts";
import * as playerUi from "../app/lib/player-ui.ts";

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

test("explains whether cancelling an active governance action spends energy", () => {
  assert.equal(typeof playerUi.governanceCancelMessage, "function");
  assert.equal(
    playerUi.governanceCancelMessage({ cost_status: "pending" }),
    "确认中止当前行动？尚未形成有效交流，不会消耗精力。",
  );
  assert.equal(
    playerUi.governanceCancelMessage({ cost_status: "committed" }),
    "确认中止当前行动？已经消耗的精力不会返还。",
  );
});

test("projects exactly the four canonical action families and keeps backend variants", () => {
  assert.equal(typeof playerUi.canonicalActionFamilies, "function");
  const projected = playerUi.canonicalActionFamilies([
    { action_id: "cadre_interview", name: "干部访谈", variants: [{ variant_id: "interview_cadre", target_choices: [{ target_id: "npc_a" }] }] },
    { action_id: "legacy_tool", name: "旧工具" },
    { action_id: "inspect_archives", name: "查阅档案", variants: [] },
    { action_id: "household_visit", name: "入户走访", variants: [{ variant_id: "field_visit", preselected_location_id: "loc_a" }] },
    { action_id: "leadership_meeting", name: "班子会议", variants: [{ variant_id: "public_hearing" }] },
  ]);
  assert.deepEqual(projected.map(item => item.action_id), [
    "household_visit", "cadre_interview", "leadership_meeting", "inspect_archives",
  ]);
  assert.equal(projected[0].variants[0].preselected_location_id, "loc_a");
  assert.equal(projected[1].variants[0].target_choices[0].target_id, "npc_a");
});

test("builds a safe people and revealed-relationship view without internal fields", () => {
  assert.equal(typeof playerUi.peopleRelationshipView, "function");
  const result = playerUi.peopleRelationshipView({
    people: [
      { npc_id: "known", name: "甲", contact_state: "known", trust_band: "working", attitude_band: "neutral", anxiety_band: "uneasy", relationship_reasons: { trust: "按公开履约记录判断", attitude: "尚未公开表态", anxiety: "仍担忧后续安置" }, recent_change_reasons: ["一", "二", "三", "四"], trust_score: 61, personality: { openness: 99 }, hidden_demands: ["秘密"] },
      { npc_id: "contact", name: "乙", contact_state: "contactable", trust_band: "trusted", attitude_band: "supportive", anxiety_band: "calm", recent_change_reasons: [] },
      { npc_id: "unknown", name: "未知", contact_state: "unknown", trust_score: 50 },
    ],
    relationship_edges: [
      { edge_id: "shown", source_npc_id: "known", target_npc_id: "contact", visibility: "suspected", channel: "同事", discovery_reason: "公开材料" },
      { edge_id: "hidden", source_npc_id: "known", target_npc_id: "unknown", visibility: "hidden", private_audit: { prompt: "secret" } },
    ],
  });
  assert.deepEqual(result.people.map(item => [item.npc_id, item.contact_state]), [["known", "known"], ["contact", "contactable"]]);
  assert.deepEqual(result.people[0].recent_change_reasons, ["一", "二", "三"]);
  assert.deepEqual(result.people[0].relationship_reasons, {
    trust: "按公开履约记录判断",
    attitude: "尚未公开表态",
    anxiety: "仍担忧后续安置",
  });
  assert.deepEqual(result.edges.map(item => item.edge_id), ["shown"]);
  const serialized = JSON.stringify(result);
  for (const forbidden of ["trust_score", "personality", "hidden_demands", "private_audit", "prompt", "unknown"]) {
    assert.doesNotMatch(serialized, new RegExp(forbidden));
  }
});

test("renders relationship bands as useful qualitative Chinese labels", () => {
  assert.equal(typeof playerUi.qualitativeRelationshipLabel, "function");
  assert.equal(playerUi.qualitativeRelationshipLabel("working"), "可协作");
  assert.equal(playerUi.qualitativeRelationshipLabel("resistant"), "抵触");
  assert.equal(playerUi.qualitativeRelationshipLabel("worried"), "担忧");
  assert.equal(playerUi.qualitativeRelationshipLabel("not_assessed"), "尚待观察");
  assert.notEqual(playerUi.qualitativeRelationshipLabel("working"), "已记录");
});

test("consumes only player-safe archive sections and ignores raw structured content", () => {
  assert.equal(typeof playerUi.archivePlayerSections, "function");
  const sections = playerUi.archivePlayerSections({
    content: JSON.stringify({ title: "内部标题", key: "deadline", value: "90天", detail: "内部细节" }),
    private_audit: "SECRET_ROOT_AUDIT",
    prompt: "SECRET_PROMPT",
    debug_notes: { summary: "SECRET_DEBUG" },
    player_sections: [
      { heading: "期限", body: "90天，到期按真实状态验收。" },
      { heading: "财政授权", body: "当前可安排7800万元。" },
    ],
  });
  assert.deepEqual(sections, [
    { heading: "期限", body: "90天，到期按真实状态验收。" },
    { heading: "财政授权", body: "当前可安排7800万元。" },
  ]);
  const rendered = JSON.stringify(sections);
  for (const forbidden of ["内部标题", "deadline", "内部细节", '"key"', '"value"', '"detail"', "SECRET_ROOT_AUDIT", "SECRET_PROMPT", "SECRET_DEBUG"]) {
    assert.doesNotMatch(rendered, new RegExp(forbidden));
  }
});

test("labels canonical and legacy conversation speakers for player review", () => {
  assert.equal(typeof playerUi.conversationSpeakerLabel, "function");
  assert.equal(playerUi.conversationSpeakerLabel({ speaker_type: "player" }, "吴秀英"), "你");
  assert.equal(playerUi.conversationSpeakerLabel({ speaker: "player" }, "吴秀英"), "你");
  assert.equal(playerUi.conversationSpeakerLabel({ speaker_type: "npc" }, "吴秀英"), "吴秀英");
});

test("uses authoritative budget-envelope labels while retaining IDs only as form values", () => {
  assert.equal(typeof playerUi.budgetEnvelopeChoices, "function");
  const choices = playerUi.budgetEnvelopeChoices({
    property_land: { label: "房屋与土地补偿", capacity: 3200, available: 2800 },
    risk_reserve: { label: "风险预备金", capacity: 500, available: 500 },
  });
  assert.deepEqual(choices.map(item => item.name), ["房屋与土地补偿", "风险预备金"]);
  assert.deepEqual(choices.map(item => item.envelope_id), ["property_land", "risk_reserve"]);
  assert.ok(choices.every(item => !item.name.includes(item.envelope_id)));
});

test("prevents overlapping confirmation submissions while preserving retries after failure", async () => {
  assert.equal(typeof playerUi.createSingleFlight, "function");
  let releases;
  let calls = 0;
  const submit = playerUi.createSingleFlight(async () => {
    calls += 1;
    await new Promise(resolve => { releases = resolve; });
    if (calls === 1) throw new Error("retryable");
    return "done";
  });
  const first = submit();
  const duplicate = submit();
  assert.strictEqual(first, duplicate);
  await Promise.resolve();
  releases();
  await assert.rejects(first, /retryable/);
  const retry = submit();
  await Promise.resolve();
  releases();
  assert.equal(await retry, "done");
  assert.equal(calls, 2);
});

test("routes people and map entries through the same canonical governance request", async () => {
  assert.equal(typeof playerUi.canonicalActionEntry, "function");
  assert.equal(typeof playerUi.submitGovernanceAction, "function");
  const descriptor = {
    action_id: "household_visit",
    variant_id: "field_visit",
    opportunity_id: "opp-d2-wu",
    participant_rules: { minimum: 1, maximum: 1 },
    location_choices: [{ location_id: "loc_village", label: "入村走访" }],
    target_choices: [{ target_id: "npc_household", label: "住户代表" }],
    canonical_topic: "听取吴秀英对搬迁安排的真实诉求",
  };
  const peopleEntry = playerUi.canonicalActionEntry({
    npc_id: "npc_household",
    canonical_action_descriptor: {
      ...descriptor,
      preselected_npc_ids: ["npc_household"],
      preselected_location_id: "loc_village",
    },
  });
  const mapEntry = playerUi.canonicalActionEntry({
    ...descriptor,
    preselected_npc_ids: ["npc_household"],
    preselected_location_id: "loc_village",
  });
  assert.deepEqual(peopleEntry, mapEntry);

  const calls = [];
  let release;
  const api = { write: (...args) => {
    calls.push(args);
    return new Promise(resolve => { release = () => resolve({ state_version: 8 }); });
  } };
  const submit = playerUi.createSingleFlight(() => playerUi.submitGovernanceAction(api, "session-1", {
    state_version: 7,
    descriptor: peopleEntry,
    location_id: "loc_tampered",
    target_ids: ["npc_tampered"],
    topic: "表单中的通用默认主题",
    archive_ids: ["archive_tampered"],
    proposed_document_type: "tampered_document",
    lead_npc_id: "npc_tampered",
  }));
  const first = submit();
  const doubleConfirm = submit();
  assert.strictEqual(first, doubleConfirm);
  await Promise.resolve();
  assert.equal(calls.length, 1);
  release();
  await first;
  const mapSubmit = playerUi.submitGovernanceAction(api, "session-1", {
    state_version: 7,
    descriptor: mapEntry,
    location_id: "loc_tampered_again",
    target_ids: ["npc_tampered_again"],
    topic: "另一个表单默认主题",
    archive_ids: ["archive_tampered_again"],
    proposed_document_type: "tampered_document",
    lead_npc_id: "npc_tampered_again",
  });
  await Promise.resolve();
  release();
  await mapSubmit;
  const canonicalRequest = ["session-1", "/governance/actions", "POST", {
    state_version: 7,
    action_kind: "household_visit",
    variant_id: "field_visit",
    location_id: "loc_village",
    opportunity_id: "opp-d2-wu",
    target_ids: ["npc_household"],
    topic: "听取吴秀英对搬迁安排的真实诉求",
    archive_ids: [],
    proposed_document_type: null,
    lead_npc_id: null,
  }];
  assert.deepEqual(calls, [canonicalRequest, canonicalRequest]);
});

test("selects exactly one dedicated primary scene for an active leadership meeting", () => {
  assert.equal(typeof playerUi.primaryScenePlan, "function");
  assert.deepEqual(playerUi.primaryScenePlan({
    has_session: true,
    active_governance_action: { action_kind: "leadership_meeting" },
    active_meeting: { meeting_id: "meeting-1" },
    active_group_conversation: null,
    active_conversation: null,
  }), ["leadership_meeting"]);
  assert.deepEqual(playerUi.primaryScenePlan({
    has_session: true,
    active_governance_action: null,
    active_meeting: null,
    active_group_conversation: null,
    active_conversation: { conversation_id: "conversation-1" },
  }), ["conversation"]);
});

test("routes retired sessions to review and never offers continue", () => {
  assert.equal(typeof playerUi.sessionEntry, "function");
  assert.deepEqual(playerUi.sessionEntry({ session_id: "old", package_status: "retired", loadable: false }), {
    session_id: "old", mode: "review", label: "仅可复盘", canContinue: false, openKind: "review", unavailableReason: "",
  });
  assert.deepEqual(playerUi.sessionEntry({ session_id: "v3", package_status: "published", loadable: true }), {
    session_id: "v3", mode: "continue", label: "继续游戏", canContinue: true, openKind: "load", unavailableReason: "",
  });
});

test("keeps unavailable content disabled and never maps it to a review request", () => {
  assert.deepEqual(playerUi.sessionEntry({
    session_id: "mismatch",
    mode: "content_unavailable",
    content_available: false,
    review_available: false,
    loadable: false,
    unavailable_reason: "该进度锁定的剧本内容已不在当前版本中，暂时无法打开。",
  }), {
    session_id: "mismatch",
    mode: "unavailable",
    label: "内容不可用",
    canContinue: false,
    openKind: null,
    unavailableReason: "该进度锁定的剧本内容已不在当前版本中，暂时无法打开。",
  });
});

test("keeps the visible conversation loop and removes the old terminal surface", async () => {
  const source = await readFile(path.join(projectRoot, "app", "GameShell.tsx"), "utf8");
  assert.match(source, />结束会谈</);
  assert.match(source, /input_mode: "conversation_end"/);
  assert.match(source, /"active-conversation-character"/);
  assert.match(source, /data-testid="active-conversation-compact"/);
  assert.match(source, /currentLine\?\.kind === "conversation_opening"/);
  assert.match(source, /currentLine\?\.speaker \? lineCharacter/);
  assert.match(source, /"gal-stage conversation-mode"/);
  assert.match(source, /className="conversation-bar gal-conversation-bar"/);
  assert.match(source, /compactCharacter\?\.role/);
  assert.match(source, /actionPointLabel\(item\)/);
  assert.match(source, /api\.loadSnapshot[\s\S]*"已载入所选关键节点", true/);
  assert.match(source, /refresh\(0, id, true, true, kind === "load" \? "latest" : "start"\)/);
  assert.match(source, /decisionReady && pending/);
  assert.match(source, /disabled=\{narrative\.currentIndex >= playerLines\.length - 1\}>下一段/);
  assert.match(source, /visibleHistoryLines\.map/);
  assert.match(source, />自定义会议主题</);
  assert.match(source, /meetingTopicMode === "custom"/);
  assert.match(source, /topic: isArchive \? "" : effectiveTopic/);
  assert.match(source, /apiError\?\.code === "STATE_VERSION_CONFLICT"/);
  assert.match(source, /required_countersign_ids/);
  assert.match(source, /chooseDocumentType/);
  assert.match(source, /会议依据（至少达到 \{requiredEvidenceLevel\}）/);
  assert.match(source, /archive_ids: isArchive \|\| \(isMeeting && Boolean\(documentType\)\) \? selectedArchives : \[\]/);
  assert.match(source, /已有一项治理行动正在进行，已为你切换到当前现场/);
  assert.match(source, /setMeetingResolutionOpen\(true\)/);
  assert.match(source, /data-testid="meeting-resolution-form"/);
  assert.match(source, /末位表态并形成决定/);
  assert.match(source, /指定分管或牵头领导/);
  assert.match(source, /lead_npc_id: requiresLead \? leadNpcId : null/);
  assert.match(source, /普通干部、村民和外部人员不能进入班子会议/);
  assert.match(source, /resource_mode: "authorization_ceiling"/);
  assert.match(source, /governance-inline-notice/);
  assert.match(source, /api\.archiveDetail\(sessionId, archiveId\)/);
  assert.match(source, /function ArchiveReading/);
  assert.match(source, /档案正文已经调出并记录查阅/);
  assert.match(source, /重读正文/);
  assert.match(source, /function clearAuthenticatedClientState\(\)/);
  assert.match(source, /if \(!restoredCsrf\) \{[\s\S]*?setAuthError\(""\)[\s\S]*?\} else \{/);
  assert.match(source, /const me = await api\.me\(\)/);
  assert.match(source, /if \(\(error as ApiError\)\?\.status !== 401\)/);
  assert.match(source, /clearAuthenticatedClientState\(\);[\s\S]*setAuthOpen\(true\)/);
  assert.match(source, /function GovernanceRecordDetail/);
  assert.match(source, /document \? "查看决议" : "查看纪要"/);
  assert.match(source, /文书 Agent 审校/);
  assert.match(source, /自动修订记录/);
  assert.match(source, /文书审校通过后才能会签/);
  assert.match(source, /\["形成文本", "文书审校", "完成会签", "正式印发", "对外公示"\]/);
  assert.match(source, /\/countersign/);
  assert.match(source, /\/issue/);
  assert.match(source, /\/publish/);
  assert.doesNotMatch(source, /function NightConversationViewer/);
  assert.doesNotMatch(source, /agent_exchanges/);
  assert.match(source, /九十日治理周期已结束/);
  assert.match(source, /查看结局复盘/);
  assert.match(source, /治理周期完成/);
  assert.match(source, /私下联络仅汇入次晨简报/);
  assert.match(source, /function NpcStreamingReplies/);
  assert.match(source, /performNpcStream/);
  assert.match(source, /\/action\/stream/);
  assert.match(source, /group-conversation\/turn\/stream/);
  assert.match(source, /governance\/meetings\/\$\{encodeURIComponent[\s\S]*\/turn\/stream/);
  assert.match(source, /governance\/actions\/\$\{encodeURIComponent[\s\S]*\/turn\/stream/);
  assert.match(source, /aria-live="polite"/);
  assert.match(source, /还需 \$\{cost\} 点精力，当前仅剩 \$\{remainingActionPoints\} 点/);
  assert.match(source, /: "填写方案"/);
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

test("keeps narrow-screen auxiliary controls touch friendly without a manual refresh button", async () => {
  const source = await readFile(path.join(projectRoot, "app", "GameShell.tsx"), "utf8");
  const styles = await readFile(path.join(projectRoot, "app", "globals.css"), "utf8");
  assert.doesNotMatch(source, /className="refresh-button"/);
  assert.match(styles, /\.history-toggle/);
  assert.match(styles, /\.icon-button/);
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
  assert.match(styles, /\.context-panel \{[\s\S]*?background: #e3d1aa/);
  assert.match(styles, /\.panel-body \{ background: #e3d1aa/);
  assert.match(styles, /\.modal-backdrop \{[^}]*background: rgba\(18,13,8,\.72\);[^}]*backdrop-filter: none/);
  assert.match(styles, /\.night-stage-shade \{[^}]*background: rgba\(16,13,10,\.24\)/);
  assert.match(styles, /\.character-profile-stage::after \{ content: none; display: none; \}/);
  assert.match(styles, /\.scene-backdrop \{ animation: scene-enter \.18s ease-out; \}/);
});

test("hides the retired day-four fatigue exposition from existing saves", async () => {
  const source = await readFile(path.join(projectRoot, "app", "GameShell.tsx"), "utf8");
  const storyBeats = await readFile(path.join(projectRoot, "..", "..", "backend", "content", "packages", "pkg_gameplay_v2", "story_beats.json"), "utf8");
  assert.match(source, /line\.blockId === "d04_source_opening"/);
  assert.doesNotMatch(storyBeats, /再补一条口径，免得和各章那句“连续满负荷降点”对不上/);
});
