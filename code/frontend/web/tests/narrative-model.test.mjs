import assert from "node:assert/strict";
import test from "node:test";

import { initialNarrativeState, narrativeItemFromFeed, narrativeReducer, pendingDecisionIsReady } from "../app/lib/narrative-model.ts";

const line = (id, cursor, text = id) => ({ id, cursor, kind: "narrative", text, contentInstanceId: `block:${id}` });

test("merges incremental feeds, de-duplicates content and follows latest by default", () => {
  let state = narrativeReducer(initialNarrativeState, { type: "SESSION_OPEN", sessionId: "s1" });
  state = narrativeReducer(state, { type: "FEED_MERGE", sessionId: "s1", items: [line("a", 1), line("b", 2)], cursor: 2 });
  assert.equal(state.items.length, 2);
  assert.equal(state.currentIndex, 1);
  assert.equal(state.unreadCount, 0);

  state = narrativeReducer(state, { type: "FEED_MERGE", sessionId: "s1", items: [line("b", 2), line("c", 3)], cursor: 3 });
  assert.deepEqual(state.items.map(item => item.id), ["a", "b", "c"]);
  assert.equal(state.currentIndex, 2);
  assert.equal(state.feedCursor, 3);
});

test("preserves backlog position, counts unread additions and returns to latest", () => {
  let state = narrativeReducer(initialNarrativeState, { type: "SESSION_REBUILD", sessionId: "s1", items: [line("a", 1), line("b", 2)], cursor: 2 });
  state = narrativeReducer(state, { type: "PREVIOUS" });
  state = narrativeReducer(state, { type: "FEED_MERGE", sessionId: "s1", items: [line("c", 3)], cursor: 3 });
  assert.equal(state.currentIndex, 0);
  assert.equal(state.unreadCount, 1);
  state = narrativeReducer(state, { type: "NEXT" });
  assert.equal(state.currentIndex, 1);
  assert.equal(state.unreadCount, 1);
  state = narrativeReducer(state, { type: "GO_LATEST" });
  assert.equal(state.currentIndex, 2);
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
  assert.equal(pendingDecisionIsReady(0, 32), false);
  assert.equal(pendingDecisionIsReady(30, 32), false);
  assert.equal(pendingDecisionIsReady(31, 32), true);
  assert.equal(pendingDecisionIsReady(-1, 0), true);
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
  }, "fallback");
  assert.equal(item.id, "block:d01_briefing_files");
  assert.equal(item.blockId, "d01_briefing_files");
  assert.equal(item.decisionId, "dp1_01_taskforce_faction_map");
  assert.equal(item.beatId, "beat_d01_arrival_and_reception");
});
