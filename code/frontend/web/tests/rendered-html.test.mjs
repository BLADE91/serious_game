import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(new Request("http://localhost/", { headers: { accept: "text/html" } }), {
    ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) },
  }, { waitUntil() {}, passThroughOnException() {} });
}

test("renders the Qingjiang governance web client", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /<title>浊流之下 · 清江搬迁记<\/title>/);
  assert.match(html, /清江搬迁记/);
  assert.match(html, /县域治理情境模拟系统/);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton|Your site is taking shape/);
});

test("sends the persisted sandbox account on player API requests", async () => {
  const source = await readFile(new URL("../app/lib/api.ts", import.meta.url), "utf8");
  assert.match(source, /this\.accountId = browserSandboxAccountId\(\)/);
  assert.match(source, /headers\["X-Account-ID"\] = this\.accountId/);
});

test("the same-origin proxy repairs requests from stale clients without an account header", async () => {
  const source = await readFile(new URL("../app/api/backend/[...path]/route.ts", import.meta.url), "utf8");
  assert.match(source, /if \(!providedAccount\) headers\.set\("X-Account-ID", anonymousAccount\)/);
  assert.match(source, /responseHeaders\.append\("set-cookie"/);
});

test("provides player controls for sorting and allocation decisions", async () => {
  const source = await readFile(new URL("../app/GameShell.tsx", import.meta.url), "utf8");
  assert.match(source, /ordered_option_ids: order/);
  assert.match(source, /parameters: \{ allocations \}/);
  assert.match(source, /allocated !== total/);
});
