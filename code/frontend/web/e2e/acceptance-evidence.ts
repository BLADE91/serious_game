import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);
const SHA256 = /^sha256:[0-9a-f]{64}$/;

export type EvidenceIdentity = { git_commit: string; workspace_fingerprint: string; v3_content_hash: string };
export type EvidenceOperation = {
  step: "resolve" | "observe_review" | "countersign" | "issue";
  operation_id: string; api_path: string; state_version_before: number; state_version_after: number;
  sequence?: number; status?: string;
};
type GitIdentity = Pick<EvidenceIdentity, "git_commit" | "workspace_fingerprint">;
type GitIdentityReader = (repository: string, allowedEvidencePaths?: string[]) => Promise<GitIdentity>;
type AuditRecord = {
  audit_id: string; session_id: string; operation_id: string; provider: string; status: string;
  error_code?: string | null;
};
export type ValidatedServerAudit = {
  source: { kind: "server_llm_audit_export"; artifact_sha256: string; exported_at: string };
  counts: { fake_calls: number; template_fallback_count: number; silent_fallback_count: number };
  audits: AuditRecord[];
};

async function gitBuffer(repository: string, ...args: string[]) {
  const { stdout } = await execFileAsync("git", args, { cwd: repository, encoding: "buffer", maxBuffer: 64 * 1024 * 1024 });
  return stdout as Buffer;
}

export async function currentGitIdentity(repository: string, allowedEvidencePaths: string[] = []): Promise<GitIdentity> {
  const repo = path.resolve(repository);
  const allowed = allowedEvidencePaths.map(item => path.relative(repo, path.resolve(item)).replaceAll("\\", "/").replace(/\/$/, ""));
  const entries = (await gitBuffer(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")).toString("utf8").split("\0").filter(Boolean);
  const dirty = entries.filter(entry => {
    const relative = entry.slice(3).replaceAll("\\", "/");
    return !entry.startsWith("?? ") || !allowed.some(root => root && (relative === root || relative.startsWith(`${root}/`)));
  });
  if (dirty.length) throw new Error(`clean worktree required for final evidence; first dirty entry: ${dirty[0]}`);
  const head = (await gitBuffer(repo, "rev-parse", "HEAD")).toString("utf8").trim();
  if (!/^[0-9a-f]{40}$/.test(head)) throw new Error("git HEAD is not a full commit SHA");
  const workspaceFingerprint = createHash("sha256").update("git-commit\0").update(head).update("\0clean-worktree\0").digest("hex");
  return { git_commit: head, workspace_fingerprint: workspaceFingerprint };
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.entries(value as Record<string, unknown>)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`).join(",")}}`;
  return JSON.stringify(value);
}

async function packageFiles(root: string, current = ""): Promise<string[]> {
  const files: string[] = [];
  for (const entry of await readdir(path.join(root, current), { withFileTypes: true })) {
    const relative = current ? `${current}/${entry.name}` : entry.name;
    if (entry.isDirectory()) files.push(...await packageFiles(root, relative));
    else if (entry.isFile()) files.push(relative);
  }
  return files;
}

export async function computeV3ContentHash(contentRoot: string): Promise<string> {
  const digest = createHash("sha256");
  for (const relative of (await packageFiles(contentRoot)).sort()) {
    digest.update(relative).update("\0");
    if (relative === "package_manifest.json") {
      const manifest = JSON.parse(await readFile(path.join(contentRoot, relative), "utf8")) as Record<string, unknown>;
      delete manifest.content_hash;
      digest.update(canonicalJson(manifest));
    } else digest.update(await readFile(path.join(contentRoot, relative)));
    digest.update("\0");
  }
  return `sha256:${digest.digest("hex")}`;
}

export async function collectEvidenceIdentity(
  repository: string, contentRoot: string,
  gitIdentity: GitIdentityReader = currentGitIdentity,
  allowedEvidencePaths: string[] = [],
): Promise<EvidenceIdentity> {
  const manifest = JSON.parse(await readFile(path.join(contentRoot, "package_manifest.json"), "utf8")) as Record<string, unknown>;
  const declared = String(manifest.content_hash || "");
  const computed = await computeV3ContentHash(contentRoot);
  if (!SHA256.test(declared) || declared !== computed) throw new Error(`v3 content hash mismatch: declared=${declared} computed=${computed}`);
  return { ...await gitIdentity(repository, allowedEvidencePaths), v3_content_hash: computed };
}

export function assertStableIdentity(start: EvidenceIdentity, end: EvidenceIdentity) {
  if (JSON.stringify(start) !== JSON.stringify(end)) throw new Error("identity changed during acceptance run");
}

export function validateServerAuditEvidence(value: unknown, sessionId: string, meetingId: string, documentId: string): ValidatedServerAudit {
  const record = value && typeof value === "object" ? value as Record<string, unknown> : {};
  const source = record.source && typeof record.source === "object" ? record.source as Record<string, unknown> : {};
  if (source.kind !== "server_llm_audit_export" || !SHA256.test(String(source.artifact_sha256 || "")) || !String(source.exported_at || "")) {
    throw new Error("formal server audit source proof is required");
  }
  if (record.session_id !== sessionId || !Array.isArray(record.audits) || !record.audits.length) {
    throw new Error("server audit export does not match the browser session");
  }
  const audits = record.audits as AuditRecord[];
  const auditIds = new Set<string>();
  for (const audit of audits) {
    if (!audit.audit_id || auditIds.has(audit.audit_id) || audit.session_id !== sessionId || !audit.operation_id || audit.status !== "success") {
      throw new Error("server audit record identity/status is invalid");
    }
    auditIds.add(audit.audit_id);
  }
  const derivedCounts = {
    fake_calls: audits.filter(audit => audit.provider.toLowerCase().includes("fake")).length,
    template_fallback_count: audits.filter(audit => String(audit.error_code || "").toLowerCase().includes("template")).length,
    silent_fallback_count: audits.filter(audit => audit.provider !== "openai_compatible").length,
  };
  const declaredCounts = record.counts && typeof record.counts === "object" ? record.counts as Record<string, unknown> : {};
  if (!["fake_calls", "template_fallback_count", "silent_fallback_count"].every(key => Number.isFinite(declaredCounts[key]))) {
    throw new Error("server audit export requires explicit numeric fallback counts");
  }
  if (JSON.stringify(declaredCounts) !== JSON.stringify(derivedCounts)) throw new Error("server audit declared counts do not match raw audits");
  if (Object.values(derivedCounts).some(count => count !== 0)) throw new Error(`fallback audit count must be zero: ${JSON.stringify(derivedCounts)}`);
  const operationIds = audits.map(audit => audit.operation_id);
  const coverage = [
    operationIds.some(id => id.startsWith(`${meetingId}:`) && id.includes("turn")),
    operationIds.includes(`${meetingId}:draft-document`),
    operationIds.some(id => id.startsWith(`${documentId}:audit:`)),
    operationIds.some(id => id.startsWith(`${documentId}:countersign:`)),
  ];
  if (coverage.some(found => !found)) throw new Error("server audit export lacks meeting/document operation coverage");
  return { source: source as ValidatedServerAudit["source"], counts: derivedCounts, audits: audits.map(audit => ({ ...audit })) };
}

export async function loadServerAuditEvidence(file: string, sessionId: string, meetingId: string, documentId: string) {
  const raw = await readFile(file);
  const value = JSON.parse(raw.toString("utf8")) as Record<string, unknown>;
  value.source = {
    kind: "server_llm_audit_export",
    artifact_sha256: `sha256:${createHash("sha256").update(raw).digest("hex")}`,
    exported_at: String(value.exported_at || ""),
  };
  return validateServerAuditEvidence(value, sessionId, meetingId, documentId);
}

export function buildAcceptanceEvidence(input: {
  identity: EvidenceIdentity; route_id: string; session_id: string; meeting_id: string; document_id: string;
  source_meeting_id: string; resolution_snapshot: Record<string, unknown>; document_status: string;
  client_steps: EvidenceOperation[]; server_audit: ValidatedServerAudit;
  console_unattributed_errors: number; network_unattributed_errors: number;
}) {
  if (!input.meeting_id || input.source_meeting_id !== input.meeting_id) throw new Error("document is not linked to its source meeting");
  if (!Object.keys(input.resolution_snapshot).length) throw new Error("document resolution snapshot is empty");
  if (input.document_status !== "issued") throw new Error(`document is not issued: ${input.document_status}`);
  const steps = input.client_steps.map(item => item.step);
  const complete = steps[0] === "resolve" && steps.includes("observe_review") && steps.at(-1) === "issue"
    && steps.slice(steps.indexOf("observe_review") + 1, -1).some(step => step === "countersign");
  if (!complete) throw new Error(`client step record is incomplete: ${steps.join(",")}`);
  if (input.console_unattributed_errors !== 0 || input.network_unattributed_errors !== 0) throw new Error("browser contains unattributed errors");
  return {
    schema_version: "browser-feature-evidence-v2", ...input.identity,
    route_id: input.route_id, session_id: input.session_id, meeting_id: input.meeting_id, document_id: input.document_id,
    source_meeting_id: input.source_meeting_id, resolution_snapshot: input.resolution_snapshot, document_status: input.document_status,
    client_steps: input.client_steps.map((item, index) => ({ ...item, sequence: index + 1, authority: "playwright_observation" })),
    server_audit_source: input.server_audit.source, server_llm_audits: input.server_audit.audits,
    counts: { ...input.server_audit.counts, console_unattributed_errors: 0, network_unattributed_errors: 0 },
  };
}
