import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

export type EvidenceIdentity = {
  git_commit: string;
  workspace_fingerprint: string;
  v3_content_hash: string;
};

export type EvidenceOperation = {
  step: "resolve" | "review" | "countersign" | "issue";
  operation_id: string;
  api_path: string;
  state_version_before: number;
  state_version_after: number;
  sequence?: number;
  status?: string;
};

type GitIdentity = Pick<EvidenceIdentity, "git_commit" | "workspace_fingerprint">;

async function gitBuffer(repository: string, ...args: string[]) {
  const { stdout } = await execFileAsync("git", args, {
    cwd: repository,
    encoding: "buffer",
    maxBuffer: 64 * 1024 * 1024,
  });
  return stdout as Buffer;
}

export async function currentGitIdentity(repository: string): Promise<GitIdentity> {
  const repo = path.resolve(repository);
  const head = (await gitBuffer(repo, "rev-parse", "HEAD")).toString("utf8").trim();
  const trackedDiff = await gitBuffer(repo, "diff", "--binary", "HEAD", "--", "code", "*.ps1", "*.bat");
  const untrackedRaw = await gitBuffer(repo, "ls-files", "--others", "--exclude-standard", "-z", "--", "code", "*.ps1", "*.bat");
  const untracked = untrackedRaw.toString("utf8").split("\0").filter(Boolean).sort();
  const digest = createHash("sha256");
  digest.update("git-commit\0").update(head).update("\0tracked-diff\0").update(trackedDiff);
  for (const relative of untracked) {
    digest.update("\0untracked-source\0").update(relative.replaceAll("\\", "/")).update("\0");
    digest.update(await readFile(path.join(repo, relative)));
  }
  return { git_commit: head, workspace_fingerprint: digest.digest("hex") };
}

export async function collectEvidenceIdentity(
  repository: string,
  contentRoot: string,
  gitIdentity: (repository: string) => Promise<GitIdentity> = currentGitIdentity,
): Promise<EvidenceIdentity> {
  const manifest = JSON.parse(await readFile(path.join(contentRoot, "package_manifest.json"), "utf8")) as Record<string, unknown>;
  const v3ContentHash = String(manifest.content_hash || "");
  if (!/^sha256:.+/.test(v3ContentHash)) throw new Error("pkg_gameplay_v3 manifest has no v3 content hash");
  return { ...await gitIdentity(repository), v3_content_hash: v3ContentHash };
}

export function buildAcceptanceEvidence(input: {
  identity: EvidenceIdentity;
  route_id: string;
  session_id: string;
  meeting_id: string;
  document_id: string;
  source_meeting_id: string;
  resolution_snapshot: Record<string, unknown>;
  document_status: string;
  operations: EvidenceOperation[];
  fake_calls?: number;
  template_fallback_count?: number;
  silent_fallback_count?: number;
  console_unattributed_errors: number;
  network_unattributed_errors: number;
}) {
  if (!input.meeting_id || input.source_meeting_id !== input.meeting_id) throw new Error("document is not linked to its source meeting");
  if (!Object.keys(input.resolution_snapshot).length) throw new Error("document resolution snapshot is empty");
  if (input.document_status !== "issued") throw new Error(`document is not issued: ${input.document_status}`);
  const steps = input.operations.map(item => item.step);
  const complete = steps[0] === "resolve"
    && steps[1] === "review"
    && steps.at(-1) === "issue"
    && steps.slice(2, -1).length > 0
    && steps.slice(2, -1).every(step => step === "countersign");
  if (!complete) {
    throw new Error(`operation record is incomplete: ${input.operations.map(item => item.step).join(",")}`);
  }
  return {
    schema_version: "browser-feature-evidence-v1",
    ...input.identity,
    route_id: input.route_id,
    session_id: input.session_id,
    meeting_id: input.meeting_id,
    document_id: input.document_id,
    source_meeting_id: input.source_meeting_id,
    resolution_snapshot: input.resolution_snapshot,
    document_status: input.document_status,
    operations: input.operations.map((item, index) => ({ ...item, sequence: index + 1 })),
    counts: {
      fake_calls: input.fake_calls ?? 0,
      template_fallback_count: input.template_fallback_count ?? 0,
      silent_fallback_count: input.silent_fallback_count ?? 0,
      console_unattributed_errors: input.console_unattributed_errors,
      network_unattributed_errors: input.network_unattributed_errors,
    },
  };
}
