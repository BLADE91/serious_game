import assert from "node:assert/strict";
import test from "node:test";

import { initialNarrativeState, narrativeItemFromFeed, narrativeReducer, pendingDecisionIsReady } from "../app/lib/narrative-model.ts";

const line = (id, cursor, text = id, storyDay = 1) => ({ id, cursor, storyDay, kind: "narrative", text, contentInstanceId: `block:${id}` });

test("standalone day headings are empty while prose and free-day guidance remain", () => {
  for (let day = 1; day <= 90; day++) {
    const intro = narrativeItemFromFeed({ kind: "day_intro", story_day: day, text: `第${day}日，第三日·被截取的标题`, content_instance_id: `day:${day}:intro` }, "unused");
    assert.equal(intro.text, "");
    assert.equal(intro.contentInstanceId, `day:${day}:intro`);
  }
  const text = "第三日，他向你说明了完整的事情经过。";
  assert.equal(narrativeItemFromFeed({ kind: "narrative", story_day: 3, text }, "scene").text, text);
  const free = "第4日，今天没有必须处理的主线事项，可以自由安排行动。";
  assert.equal(narrativeItemFromFeed({ kind: "day_intro", story_day: 4, text: free }, "free").text, free.replace("第4日，", ""));
});

test("merges incremental feeds, de-duplicates content and stops at the first unread item", () => {
  let state = narrativeReducer(initialNarrativeState, { type: "SESSION_OPEN", sessionId: "s1" });
  state = narrativeReducer(state, { type: "FEED_MERGE", sessionId: "s1", items: [line("a", 1), line("b", 2)], cursor: 2 });
  assert.equal(state.items.length, 2);
  assert.equal(state.currentIndex, 0);
  assert.equal(state.unreadCount, 1);

  state = narrativeReducer(state, { type: "FEED_MERGE", sessionId: "s1", items: [line("b", 2), line("c", 3)], cursor: 3 });
  assert.deepEqual(state.items.map(item => item.id), ["a", "b", "c"]);
  assert.equal(state.currentIndex, 0);
  assert.equal(state.feedCursor, 3);
});

test("keeps navigation inside the current day and separates prior days", () => {
  let state = narrativeReducer(initialNarrativeState, { type: "SESSION_REBUILD", sessionId: "s1", items: [line("a", 1), line("b", 2)], cursor: 2 });
  state = narrativeReducer(state, { type: "PREVIOUS" });
  state = narrativeReducer(state, { type: "FEED_MERGE", sessionId: "s1", items: [line("c", 3, "c", 2), line("d", 4, "d", 2)], cursor: 4 });
  assert.equal(state.currentIndex, 0);
  assert.equal(state.unreadCount, 1);
  assert.deepEqual(state.items.map(item => item.id), ["c", "d"]);
  assert.deepEqual(state.historyItems.map(item => item.id), ["a", "b"]);
  state = narrativeReducer(state, { type: "NEXT" });
  assert.equal(state.currentIndex, 1);
  assert.equal(state.unreadCount, 0);
  state = narrativeReducer(state, { type: "GO_LATEST" });
  assert.equal(state.currentIndex, 1);
  assert.equal(state.unreadCount, 0);
});

test("resets for another save and rebuilds authoritative feed after a 409", () => {
  let state = narrativeReducer(initialNarrativeState, { type: "SESSION_REBUILD", sessionId: "save-a", items: [line("old", 7)], cursor: 7 });
  state = narrativeReducer(state, { type: "SESSION_OPEN", sessionId: "save-b" });
  assert.equal(state.sessionId, "save-b");
  assert.deepEqual(state.items, []);
  state = narrativeReducer(state, { type: "SESSION_REBUILD", sessionId: "save-b", items: [line("fresh", 1), line("fresh", 1)], cursor: 1 });
  assert.deepEqual(state.items.map(item => item.id), ["fresh"]);
  assert.equal(state.currentIndex, 0);
  assert.equal(state.rebuildCount, 1);
});

test("starts a brand-new game at the first story entry while restores stay latest", () => {
  const items = [line("arrival", 1), line("office", 2), line("phone", 3)];
  const started = narrativeReducer(initialNarrativeState, { type: "SESSION_REBUILD", sessionId: "new-game", items, cursor: 3, position: "start" });
  const restored = narrativeReducer(initialNarrativeState, { type: "SESSION_REBUILD", sessionId: "saved-game", items, cursor: 3, position: "latest" });
  assert.equal(started.currentIndex, 0);
  assert.equal(started.items[started.currentIndex].id, "arrival");
  assert.equal(restored.currentIndex, 2);
  assert.equal(restored.items[restored.currentIndex].id, "phone");
});

test("reveals a pending decision only after its current narrative has been read", () => {
  const setup = { contentInstanceId: "block:setup", presentationPhase: "decision_setup" };
  const card = { contentInstanceId: "decision:event-1", presentationPhase: "decision" };
  assert.equal(pendingDecisionIsReady(setup, "decision:event-1"), false);
  assert.equal(pendingDecisionIsReady(card, "decision:event-1"), true);
  assert.equal(pendingDecisionIsReady(card, "decision:event-2"), false);
  assert.equal(pendingDecisionIsReady(null, "decision:event-1"), false);
});

test("keeps optional feed metadata for compatibility and stable scene matching", () => {
  const item = narrativeItemFromFeed({
    cursor: 4,
    story_day: 12,
    kind: "dialogue",
    speaker: "郑向东",
    text: "请看卷宗。",
    content_instance_id: "block:d01_briefing_files",
    block_id: "d01_briefing_files",
    decision_id: "dp1_01_taskforce_faction_map",
    beat_id: "beat_d01_arrival_and_reception",
    scene_id: "C01_S02",
    presentation_phase: "opening",
    day_sequence: 2,
    read_gate: "continue",
  }, "fallback");
  assert.equal(item.id, "block:d01_briefing_files");
  assert.equal(item.blockId, "d01_briefing_files");
  assert.equal(item.decisionId, "dp1_01_taskforce_faction_map");
  assert.equal(item.beatId, "beat_d01_arrival_and_reception");
  assert.equal(item.sceneId, "C01_S02");
  assert.equal(item.presentationPhase, "opening");
});


test("merges the free-day introduction into its morning card without repeated dates", () => {
  const morning = narrativeItemFromFeed({kind: "morning_card", story_day: 4, text: "D4 清晨，专班完成了昨日材料结转。", content_instance_id: "morning:4"}, "morning");
  const intro = narrativeItemFromFeed({kind: "day_intro", story_day: 4, text: "第4日，今天没有必须处理的主线事项，可以自由安排行动。", content_instance_id: "day:4:intro"}, "intro");
  let state = narrativeReducer(initialNarrativeState, {type: "SESSION_REBUILD", sessionId: "d4", items: [morning, intro], cursor: 2});
  assert.equal(state.items.length, 1);
  assert.equal(state.items[0].text, "清晨，专班完成了昨日材料结转。\n今天没有必须处理的主线事项，可以自由安排行动。");
  state = narrativeReducer(state, {type: "FEED_MERGE", sessionId: "d4", items: [morning, intro], cursor: 2});
  assert.equal(state.items.length, 1);
  assert.equal(state.items[0].text.split("今天").length, 2);
});
