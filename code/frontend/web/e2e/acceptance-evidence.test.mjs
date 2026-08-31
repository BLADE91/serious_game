import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { promisify } from "node:util";

import {
  assertStableIdentity,
  buildAcceptanceEvidence,
  collectAuditPages,
  collectEvidenceIdentity,
  computeV3ContentHash,
  currentGitIdentity,
  validateLiveServerAuditEvidence,
} from "./acceptance-evidence.ts";

const execFileAsync = promisify(execFile);

test("collectEvidenceIdentity recomputes and verifies the declared v3 hash", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "browser-evidence-identity-"));
  const content = path.join(root, "content");
  await mkdir(content);
  const manifestPath = path.join(content, "package_manifest.json");
  await writeFile(manifestPath, JSON.stringify({
    package_id: "pkg_gameplay_v3",
    status: "published",
    content_hash: `sha256:${"0".repeat(64)}`,
  }));
  const computed = await computeV3ContentHash(content);
  await writeFile(manifestPath, JSON.stringify({ package_id: "pkg_gameplay_v3", status: "published", content_hash: computed }));

  const identity = await collectEvidenceIdentity(root, content, async () => ({
    git_commit: "0123456789abcdef0123456789abcdef01234567",
    workspace_fingerprint: "f".repeat(64),
  }));

  assert.deepEqual(identity, {
    git_commit: "0123456789abcdef0123456789abcdef01234567",
    workspace_fingerprint: "f".repeat(64),
    v3_content_hash: computed,
  });

  await writeFile(manifestPath, JSON.stringify({ package_id: "pkg_gameplay_v3", status: "published", content_hash: `sha256:${"a".repeat(64)}` }));
  await assert.rejects(() => collectEvidenceIdentity(root, content, async () => ({
    git_commit: "0123456789abcdef0123456789abcdef01234567",
    workspace_fingerprint: "f".repeat(64),
  })), /v3 content hash mismatch/);
});

test("currentGitIdentity rejects a dirty tracked worktree", async () => {
  const repo = await mkdtemp(path.join(os.tmpdir(), "browser-evidence-git-"));
  await execFileAsync("git", ["init", "-q"], { cwd: repo });
  await execFileAsync("git", ["config", "user.email", "e2e@example.invalid"], { cwd: repo });
  await execFileAsync("git", ["config", "user.name", "E2E"], { cwd: repo });
  await writeFile(path.join(repo, "tracked.txt"), "clean\n");
  await execFileAsync("git", ["add", "tracked.txt"], { cwd: repo });
  await execFileAsync("git", ["commit", "-qm", "fixture"], { cwd: repo });
  const clean = await currentGitIdentity(repo);
  assert.match(clean.git_commit, /^[0-9a-f]{40}$/);
  await writeFile(path.join(repo, "tracked.txt"), "dirty\n");
  await assert.rejects(() => currentGitIdentity(repo), /clean worktree required/);
});

test("currentGitIdentity permits only untracked files beneath the explicit evidence output", async () => {
  const repo = await mkdtemp(path.join(os.tmpdir(), "browser-evidence-allowlist-"));
  await execFileAsync("git", ["init", "-q"], { cwd: repo });
  await execFileAsync("git", ["config", "user.email", "e2e@example.invalid"], { cwd: repo });
  await execFileAsync("git", ["config", "user.name", "E2E"], { cwd: repo });
  await writeFile(path.join(repo, "tracked.txt"), "clean\n");
  await execFileAsync("git", ["add", "tracked.txt"], { cwd: repo });
  await execFileAsync("git", ["commit", "-qm", "fixture"], { cwd: repo });
  const evidenceDir = path.join(repo, "output", "run-1");
  await mkdir(evidenceDir, { recursive: true });
  await writeFile(path.join(evidenceDir, "trace.json"), "{}\n");
  await currentGitIdentity(repo, [evidenceDir]);
  await writeFile(path.join(repo, "untracked-source.ts"), "export {};\n");
  await assert.rejects(() => currentGitIdentity(repo, [evidenceDir]), /untracked-source\.ts/);
});

test("assertStableIdentity rejects a source or content change during the run", () => {
  const start = { git_commit: "a".repeat(40), workspace_fingerprint: "b".repeat(64), v3_content_hash: `sha256:${"c".repeat(64)}` };
  assert.throws(() => assertStableIdentity(start, { ...start, v3_content_hash: `sha256:${"d".repeat(64)}` }), /identity changed during acceptance run/);
});

const validAuditPage = () => ({
  run_nonce: "session-1",
  session_id: "session-1",
  next_cursor: "2026-08-31T12:00:04Z|llm_sign",
  audits: [
    { audit_id: "llm_turn", session_id: "session-1", run_id: "session-1", operation_id: "m-1:turn:1:npc-1", provider: "openai_compatible", model: "model-1", endpoint_host: "api.example.test", config_version: "cfg-1", status: "success", error_code: null, timestamp: "2026-08-31T12:00:01Z" },
    { audit_id: "llm_draft", session_id: "session-1", run_id: "session-1", operation_id: "m-1:draft-document", provider: "openai_compatible", model: "model-1", endpoint_host: "api.example.test", config_version: "cfg-1", status: "success", error_code: null, timestamp: "2026-08-31T12:00:02Z" },
    { audit_id: "llm_review", session_id: "session-1", run_id: "session-1", operation_id: `d-1:audit:v1:sha256:${"a".repeat(64)}`, provider: "openai_compatible", model: "model-1", endpoint_host: "api.example.test", config_version: "cfg-1", status: "success", error_code: null, timestamp: "2026-08-31T12:00:03Z" },
    { audit_id: "llm_sign", session_id: "session-1", run_id: "session-1", operation_id: "d-1:countersign:npc-1:v1", provider: "openai_compatible", model: "model-1", endpoint_host: "api.example.test", config_version: "cfg-1", status: "success", error_code: null, timestamp: "2026-08-31T12:00:04Z" },
  ],
});

const auditExpectation = () => ({
  session_id: "session-1", meeting_id: "m-1", document_id: "d-1",
  started_at: "2026-08-31T12:00:00Z", ended_at: "2026-08-31T12:00:05Z",
  endpoint_host: "api.example.test", config_version: "cfg-1", model: "model-1",
});

test("collectAuditPages follows cursors until the authenticated endpoint returns an empty page", async () => {
  const seen = [];
  const pages = await collectAuditPages(async cursor => {
    seen.push(cursor);
    if (cursor === "baseline") return { ...validAuditPage(), audits: validAuditPage().audits.slice(0, 2), next_cursor: "cursor-2" };
    if (cursor === "cursor-2") return { ...validAuditPage(), audits: validAuditPage().audits.slice(2), next_cursor: "cursor-4" };
    return { run_nonce: "session-1", session_id: "session-1", next_cursor: cursor, audits: [] };
  }, "baseline");
  assert.deepEqual(seen, ["baseline", "cursor-2", "cursor-4"]);
  assert.equal(pages.length, 3);
});

test("validateLiveServerAuditEvidence rejects cross-session, stale, missing, and mixed-config records", () => {
  const cases = [
    [page => { page.audits[0].session_id = "other"; }, /same authenticated session/],
    [page => { page.audits[0].timestamp = "2026-08-30T12:00:00Z"; }, /current operation window/],
    [page => { delete page.audits[0].audit_id; }, /required audit fields/],
    [page => { page.audits[0].config_version = "cfg-other"; }, /current AI configuration/],
    [page => { page.audits[0].endpoint_host = "other.example.test"; }, /current AI configuration/],
  ];
  for (const [mutate, pattern] of cases) {
    const page = validAuditPage();
    mutate(page);
    assert.throws(() => validateLiveServerAuditEvidence([page], auditExpectation()), pattern);
  }
  for (const mutation of [{ provider: "fake" }, { error_code: "template_fallback" }, { provider: "silent_fallback" }]) {
    const page = validAuditPage();
    Object.assign(page.audits[0], mutation);
    assert.throws(() => validateLiveServerAuditEvidence([page], auditExpectation()), /fallback audit count must be zero/);
  }
});

test("buildAcceptanceEvidence emits client steps separately from formal server audits", () => {
  const operations = [
    { step: "resolve", operation_id: "resolve-1", api_path: "/meetings/m-1/resolve", state_version_before: 3, state_version_after: 4 },
    { step: "observe_review", operation_id: "review-1", api_path: "/documents/d-1", state_version_before: 4, state_version_after: 5 },
    { step: "countersign", operation_id: "sign-1", api_path: "/documents/d-1/countersign", state_version_before: 5, state_version_after: 6 },
    { step: "issue", operation_id: "issue-1", api_path: "/documents/d-1/issue", state_version_before: 6, state_version_after: 7 },
  ];

  const evidence = buildAcceptanceEvidence({
    identity: {
      git_commit: "0123456789abcdef0123456789abcdef01234567",
      workspace_fingerprint: "f".repeat(64),
      v3_content_hash: `sha256:${"c".repeat(64)}`,
    },
    route_id: "feature-leadership-meeting",
    session_id: "session-1",
    meeting_id: "m-1",
    document_id: "d-1",
    source_meeting_id: "m-1",
    resolution_snapshot: { decision: "依法形成决议" },
    document_status: "issued",
    client_steps: operations,
    server_audit: validateLiveServerAuditEvidence([validAuditPage()], auditExpectation()),
    console_unattributed_errors: 0,
    network_unattributed_errors: 0,
  });

  assert.deepEqual(evidence.counts, {
    fake_calls: 0,
    template_fallback_count: 0,
    silent_fallback_count: 0,
    console_unattributed_errors: 0,
    network_unattributed_errors: 0,
  });
  assert.deepEqual(evidence.client_steps.map(item => item.sequence), [1, 2, 3, 4]);
  assert.deepEqual(evidence.client_steps.map(item => item.step), ["resolve", "observe_review", "countersign", "issue"]);
  assert.equal(evidence.server_llm_audits[0].audit_id, "llm_turn");
  assert.equal(evidence.source_meeting_id, evidence.meeting_id);
  assert.deepEqual(evidence.resolution_snapshot, { decision: "依法形成决议" });
});
