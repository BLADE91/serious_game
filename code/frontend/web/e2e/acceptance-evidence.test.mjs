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
  collectEvidenceIdentity,
  computeV3ContentHash,
  currentGitIdentity,
  validateServerAuditEvidence,
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

const validServerAudit = () => ({
  source: {
    kind: "server_llm_audit_export",
    artifact_sha256: `sha256:${"e".repeat(64)}`,
    exported_at: "2026-08-31T12:00:00Z",
  },
  session_id: "session-1",
  counts: { fake_calls: 0, template_fallback_count: 0, silent_fallback_count: 0 },
  audits: [
    { audit_id: "llm_turn", session_id: "session-1", operation_id: "m-1:turn:1", provider: "openai_compatible", status: "success", error_code: null },
    { audit_id: "llm_draft", session_id: "session-1", operation_id: "m-1:draft-document", provider: "openai_compatible", status: "success", error_code: null },
    { audit_id: "llm_review", session_id: "session-1", operation_id: "d-1:audit:v1:initial", provider: "openai_compatible", status: "success", error_code: null },
    { audit_id: "llm_sign", session_id: "session-1", operation_id: "d-1:countersign:npc-1", provider: "openai_compatible", status: "success", error_code: null },
  ],
});

test("validateServerAuditEvidence rejects missing source and every non-zero fallback count", () => {
  assert.throws(() => validateServerAuditEvidence({}, "session-1", "m-1", "d-1"), /formal server audit source/);
  for (const [mutation, countKey] of [
    [{ provider: "fake" }, "fake_calls"],
    [{ error_code: "template_fallback" }, "template_fallback_count"],
    [{ provider: "silent_fallback" }, "silent_fallback_count"],
  ]) {
    const evidence = validServerAudit();
    Object.assign(evidence.audits[0], mutation);
    evidence.counts[countKey] = 1;
    if (mutation.provider === "fake") evidence.counts.silent_fallback_count = 1;
    assert.throws(() => validateServerAuditEvidence(evidence, "session-1", "m-1", "d-1"), /fallback audit count must be zero/);
  }
  const missingCount = validServerAudit();
  delete missingCount.counts.template_fallback_count;
  assert.throws(() => validateServerAuditEvidence(missingCount, "session-1", "m-1", "d-1"), /explicit numeric fallback counts/);
  const tamperedCount = validServerAudit();
  tamperedCount.counts.fake_calls = 1;
  assert.throws(() => validateServerAuditEvidence(tamperedCount, "session-1", "m-1", "d-1"), /declared counts do not match raw audits/);
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
    server_audit: validateServerAuditEvidence(validServerAudit(), "session-1", "m-1", "d-1"),
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
