import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { GameApi } from "../app/lib/api.ts";
import * as playerUi from "../app/lib/player-ui.ts";

test("parses incremental NPC events and returns the authoritative result", async () => {
  const previousFetch = globalThis.fetch;
  const encoded = new TextEncoder();
  const chunks = [
    '{"type":"stream_start"}\n{"type":"npc_start","stream_id":"npc:0","npc_id":"npc"}\n',
    '{"type":"npc_delta","stream_id":"npc:0","delta":"逐字"}\n',
    '{"type":"npc_delta","stream_id":"npc:0","delta":"回应"}\n{"type":"npc_end","stream_id":"npc:0"}\n',
    '{"type":"complete","result":{"state_version":3}}\n',
  ];
  globalThis.fetch = async () => new Response(new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoded.encode(chunk));
      controller.close();
    },
  }), { status: 200, headers: { "Content-Type": "application/x-ndjson" } });

  try {
    const api = new GameApi("/api/backend");
    const events = [];
    const result = await api.streamWrite("session 1", "/action/stream", {
      player_text: "测试",
    }, event => events.push(event));
    assert.deepEqual(events.map(event => event.type), [
      "stream_start", "npc_start", "npc_delta", "npc_delta", "npc_end", "complete",
    ]);
    assert.equal(events.filter(event => event.type === "npc_delta").map(event => event.delta).join(""), "逐字回应");
    assert.deepEqual(result, { state_version: 3 });
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("tracks thinking per NPC, progressive answers, and safe error cleanup", () => {
  assert.equal(typeof playerUi.reduceNpcStream, "function");
  let state = playerUi.initialNpcStreamState();
  for (const event of [
    { type: "npc_thinking_start", stream_id: "a:0", npc_id: "a", npc_name: "甲" },
    { type: "npc_thinking_end", stream_id: "a:0", npc_id: "a", npc_name: "甲" },
    { type: "npc_start", stream_id: "a:0", npc_id: "a", npc_name: "甲" },
    { type: "npc_delta", stream_id: "a:0", delta: "答" },
    { type: "npc_delta", stream_id: "a:0", delta: "复" },
    { type: "npc_end", stream_id: "a:0", npc_id: "a" },
  ]) state = playerUi.reduceNpcStream(state, event);
  assert.deepEqual(state.thinking, {});
  assert.deepEqual(state.replies, [{ stream_id: "a:0", npc_id: "a", npc_name: "甲", text: "答复", complete: true }]);
  state = playerUi.reduceNpcStream(state, { type: "npc_thinking_start", stream_id: "b:0", npc_id: "b", npc_name: "乙" });
  state = playerUi.reduceNpcStream(state, { type: "error", code: "NPC_RESPONSE_UNAVAILABLE", message: "对方暂时无法回应，请稍后重试。" });
  assert.deepEqual(state.thinking, {});
  assert.equal(state.error, "对方暂时无法回应，请稍后重试。");
});

test("styles each rendered NPC thinking row instead of an absent child element", async () => {
  const css = await readFile(new URL("../app/globals.css", import.meta.url), "utf8");
  assert.match(css, /\.npc-thinking-status\s*>\s*div\s*\{/);
});

test("loads complete paginated conversation history with stable filters", async () => {
  const previousFetch = globalThis.fetch;
  const requested = [];
  globalThis.fetch = async url => {
    requested.push(String(url));
    const second = String(url).includes("cursor=next-page");
    return Response.json(second
      ? { items: [{ conversation_id: "c2", transcript: [{ speaker_type: "npc", text: "完整第二页" }] }], next_cursor: null }
      : { items: [{ conversation_id: "c1", transcript: [{ speaker_type: "player", text: "第一页" }] }], next_cursor: "next-page" });
  };
  try {
    const api = new GameApi("/api/backend");
    assert.equal(typeof api.completeConversationHistory, "function");
    const items = await api.completeConversationHistory("session 1", { npc_id: "npc a", story_day: 8, limit: 1 });
    assert.deepEqual(items.map(item => item.conversation_id), ["c1", "c2"]);
    assert.equal(requested.length, 2);
    assert.match(requested[0], /npc_id=npc\+a/);
    assert.match(requested[0], /story_day=8/);
    assert.match(requested[1], /cursor=next-page/);
  } finally {
    globalThis.fetch = previousFetch;
  }
});
