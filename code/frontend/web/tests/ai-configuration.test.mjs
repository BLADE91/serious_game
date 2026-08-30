import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

import { GameApi } from "../app/lib/api.ts";
import * as playerUi from "../app/lib/player-ui.ts";

const shellPath = new URL("../app/GameShell.tsx", import.meta.url);
const stylesPath = new URL("../app/globals.css", import.meta.url);


test("projects the login AI step without ever requiring a stored key", () => {
  assert.equal(typeof playerUi.aiConfigurationView, "function");
  assert.deepEqual(playerUi.aiConfigurationView({
    mode: "unconfigured",
    active: false,
    server_default_available: true,
    server_default: { endpoint: "api.example", model: "default-model" },
  }), {
    configured: false,
    mode: "unconfigured",
    summary: "尚未配置 AI 接口",
    serverDefaultAvailable: true,
    serverDefaultSummary: "api.example · default-model",
    compatibilityStatus: "untested",
    capabilities: [],
    testedAt: "",
  });
  assert.deepEqual(playerUi.aiConfigurationView({
    mode: "personal",
    active: true,
    endpoint: "personal.example",
    model: "player-model",
    api_key: "must-not-project",
    compatibility_status: "compatible",
    capabilities: {
      single_choice: "passed", multiple_choice: "passed", expression: "passed",
      night_followup: "passed", contract_rendering: "passed", document_rendering: "passed",
    },
    tested_at: "2026-08-24T10:00:00+00:00",
  }), {
    configured: true,
    mode: "personal",
    summary: "个人 API · personal.example · player-model",
    serverDefaultAvailable: false,
    serverDefaultSummary: "",
    compatibilityStatus: "compatible",
    capabilities: [
      "单选", "多选", "人物表达", "夜间与后续会谈", "合同转写", "行政文书转写",
    ],
    testedAt: "2026-08-24T10:00:00+00:00",
  });
});

test("recognizes configuration-required model failures", () => {
  assert.equal(typeof playerUi.requiresAIConfiguration, "function");
  assert.equal(playerUi.requiresAIConfiguration({ code: "ROLE_LLM_CONFIGURATION_REQUIRED" }), true);
  assert.equal(playerUi.requiresAIConfiguration({ code: "ROLE_LLM_UNAVAILABLE" }), false);
});

test("shows safe actionable AI configuration errors", () => {
  assert.equal(typeof playerUi.aiConfigurationErrorMessage, "function");
  assert.equal(playerUi.aiConfigurationErrorMessage({
    code: "ROLE_LLM_CONFIGURATION_INVALID",
    status: 422,
    message: "API Key 无效，或该账号没有模型权限",
  }), "API Key 无效，或该账号没有模型权限");
  assert.equal(playerUi.aiConfigurationErrorMessage({
    code: "ROLE_LLM_CONFIGURATION_INVALID",
    status: 422,
    message: "AI 接口连接超时或暂时不可用",
  }), "AI 接口连接超时或暂时不可用");
  assert.equal(playerUi.aiConfigurationErrorMessage({
    code: "ROLE_LLM_CONFIGURATION_INVALID",
    status: 422,
    message: "player-key-secret",
  }), "AI 接口测试失败，请检查地址、Key 和模型名后重试。");
});

test("keeps an explicit API test result visible until the player continues", async () => {
  const source = await readFile(shellPath, "utf8");
  const configureBody = source.match(/async function configureAI[\s\S]*?\n  function continueAfterAIConfiguration/)?.[0] || "";

  assert.match(source, /接口测试成功/);
  assert.match(source, /接口测试失败/);
  assert.match(source, /success && view\.configured[\s\S]*?onContinue/);
  assert.doesNotMatch(configureBody, /setAuthOpen\(false\)/);
  assert.doesNotMatch(configureBody, /setSessionOpen\(true\)/);
});

test("places the welcome action on a separate row below the credits", async () => {
  const [source, styles] = await Promise.all([
    readFile(shellPath, "utf8"),
    readFile(stylesPath, "utf8"),
  ]);

  assert.match(source, /className="welcome-credits"[\s\S]*?开发：/);
  assert.match(source, /className="welcome-action"[\s\S]*?接下调令，前往云溪/);
  assert.match(styles, /\.welcome-action\s*\{[^}]*display:\s*(?:flex|grid|block)/);
});

test("uses authenticated API configuration endpoints without browser persistence", async () => {
  const requests = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    requests.push({ url: String(url), method: init.method, body: init.body });
    return new Response(JSON.stringify({
      mode: requests.length === 1 ? "unconfigured" : "personal",
      active: requests.length > 1,
      endpoint: requests.length > 1 ? "personal.example" : null,
      model: requests.length > 1 ? "player-model" : null,
      server_default_available: true,
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  };
  try {
    const api = new GameApi("/api/backend");
    api.setAccountId("account-a");
    api.setCsrfToken("csrf-a");
    const initial = await api.aiConfiguration();
    assert.equal(initial.mode, "unconfigured");
    const configured = await api.configureAI({
      mode: "personal",
      base_url: "https://personal.example/v1",
      api_key: "ephemeral-key",
      model: "player-model",
    });
    assert.equal(configured.mode, "personal");
    await api.clearAIConfiguration();
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.deepEqual(requests.map(item => [item.method, item.url]), [
    ["GET", "/api/backend/api/ai/config"],
    ["PUT", "/api/backend/api/ai/config"],
    ["DELETE", "/api/backend/api/ai/config"],
  ]);
  assert.match(String(requests[1].body), /ephemeral-key/);
  assert.equal(typeof sessionStorage, "undefined");
});

test("persists only the CSRF token in a same-site cookie so an authenticated browser can reopen", () => {
  const originalDocument = globalThis.document;
  const originalSessionStorage = globalThis.sessionStorage;
  const stored = new Map();
  let cookie = "";
  globalThis.sessionStorage = {
    setItem(key, value) { stored.set(key, value); },
    getItem(key) { return stored.get(key) || null; },
    removeItem(key) { stored.delete(key); },
  };
  globalThis.document = {
    get cookie() { return cookie; },
    set cookie(value) { cookie = value; },
  };
  try {
    const api = new GameApi("/api/backend");
    api.setCsrfToken("csrf value", "custom_csrf");
    assert.equal(stored.get("qingjiang-csrf"), "csrf value");
    assert.match(cookie, /^custom_csrf=csrf%20value; Path=\/; SameSite=Lax$/);
  } finally {
    if (originalDocument === undefined) delete globalThis.document;
    else globalThis.document = originalDocument;
    if (originalSessionStorage === undefined) delete globalThis.sessionStorage;
    else globalThis.sessionStorage = originalSessionStorage;
  }
});
