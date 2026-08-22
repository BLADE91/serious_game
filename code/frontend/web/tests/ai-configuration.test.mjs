import assert from "node:assert/strict";
import test from "node:test";

import { GameApi } from "../app/lib/api.ts";
import * as playerUi from "../app/lib/player-ui.ts";


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
  });
  assert.deepEqual(playerUi.aiConfigurationView({
    mode: "personal",
    active: true,
    endpoint: "personal.example",
    model: "player-model",
    api_key: "must-not-project",
  }), {
    configured: true,
    mode: "personal",
    summary: "个人 API · personal.example · player-model",
    serverDefaultAvailable: false,
    serverDefaultSummary: "",
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
