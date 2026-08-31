import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { buildAcceptanceEvidence, collectEvidenceIdentity } from "./acceptance-evidence.ts";

test("collectEvidenceIdentity binds evidence to git SHA, workspace fingerprint, and v3 hash", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "browser-evidence-identity-"));
  const content = path.join(root, "content");
  await mkdir(content);
  await writeFile(path.join(content, "package_manifest.json"), JSON.stringify({
    package_id: "pkg_gameplay_v3",
    content_hash: "sha256:fixture-v3-hash",
  }));

  const identity = await collectEvidenceIdentity(root, content, async () => ({
    git_commit: "0123456789abcdef0123456789abcdef01234567",
    workspace_fingerprint: "f".repeat(64),
  }));

  assert.deepEqual(identity, {
    git_commit: "0123456789abcdef0123456789abcdef01234567",
    workspace_fingerprint: "f".repeat(64),
    v3_content_hash: "sha256:fixture-v3-hash",
  });
});

test("buildAcceptanceEvidence emits ordered operations and explicit zero fallback/error counts", () => {
  const operations = [
    { step: "resolve", operation_id: "resolve-1", api_path: "/meetings/m-1/resolve", state_version_before: 3, state_version_after: 4 },
    { step: "review", operation_id: "review-1", api_path: "/documents/d-1", state_version_before: 4, state_version_after: 5 },
    { step: "countersign", operation_id: "sign-1", api_path: "/documents/d-1/countersign", state_version_before: 5, state_version_after: 6 },
    { step: "issue", operation_id: "issue-1", api_path: "/documents/d-1/issue", state_version_before: 6, state_version_after: 7 },
  ];

  const evidence = buildAcceptanceEvidence({
    identity: {
      git_commit: "0123456789abcdef0123456789abcdef01234567",
      workspace_fingerprint: "f".repeat(64),
      v3_content_hash: "sha256:fixture-v3-hash",
    },
    route_id: "feature-leadership-meeting",
    session_id: "session-1",
    meeting_id: "m-1",
    document_id: "d-1",
    source_meeting_id: "m-1",
    resolution_snapshot: { decision: "依法形成决议" },
    document_status: "issued",
    operations,
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
  assert.deepEqual(evidence.operations.map(item => item.sequence), [1, 2, 3, 4]);
  assert.deepEqual(evidence.operations.map(item => item.step), ["resolve", "review", "countersign", "issue"]);
  assert.equal(evidence.source_meeting_id, evidence.meeting_id);
  assert.deepEqual(evidence.resolution_snapshot, { decision: "依法形成决议" });
});
