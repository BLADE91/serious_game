import assert from "node:assert/strict";
import test from "node:test";

import { GameApi } from "../app/lib/api.ts";

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
