from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys
from typing import Callable, Iterable


Stage = tuple[str, tuple[str, ...]]
StageExecutor = Callable[[str, tuple[str, ...]], int]


def _git_bytes(repository: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=repository,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(
            completed.stderr.decode("utf-8", errors="replace").strip()
            or f"git {' '.join(arguments)} failed"
        )
    return completed.stdout


def workspace_fingerprint(repository: Path) -> dict[str, object]:
    """Bind evidence to the exact tracked diff and untracked runtime sources."""

    repository = repository.resolve()
    head = _git_bytes(repository, "rev-parse", "HEAD").decode().strip()
    tracked_diff = _git_bytes(
        repository,
        "diff",
        "--binary",
        "HEAD",
        "--",
        "code",
        "*.ps1",
        "*.bat",
    )
    untracked_raw = _git_bytes(
        repository,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        "--",
        "code",
        "*.ps1",
        "*.bat",
    )
    untracked = sorted(
        path.decode("utf-8", errors="surrogateescape")
        for path in untracked_raw.split(b"\0")
        if path
    )
    digest = sha256()
    digest.update(b"git-commit\0")
    digest.update(head.encode("ascii"))
    digest.update(b"\0tracked-diff\0")
    digest.update(tracked_diff)
    for relative in untracked:
        source = repository / relative
        if not source.is_file():
            continue
        digest.update(b"\0untracked-source\0")
        digest.update(relative.replace("\\", "/").encode("utf-8"))
        digest.update(b"\0")
        digest.update(source.read_bytes())
    return {
        "git_commit": head,
        "workspace_fingerprint": digest.hexdigest(),
        "untracked_runtime_source_count": len(untracked),
    }


def run_stages(
    stages: Iterable[Stage],
    *,
    executor: StageExecutor,
) -> None:
    """Run acceptance stages in order and stop on the first failure."""

    for name, command in stages:
        exit_code = executor(name, command)
        if exit_code != 0:
            raise SystemExit(
                f"full acceptance stopped at {name} (exit code {exit_code})"
            )


def _default_stages(backend_root: Path, run_root: Path) -> tuple[Stage, ...]:
    tools = backend_root / "tools"
    profiles = (
        backend_root
        / "content"
        / "packages"
        / "pkg_gameplay_v3"
        / "acceptance_route_profiles.json"
    )
    python = sys.executable
    return (
        (
            "capabilities",
            (
                python,
                str(tools / "run_choice_expression_live_matrix.py"),
                "--output-dir",
                str(run_root / "capabilities"),
            ),
        ),
        (
            "roles",
            (
                python,
                str(tools / "run_m3_live_role_matrix.py"),
                "--output-dir",
                str(run_root / "roles"),
            ),
        ),
        (
            "failures",
            (
                python,
                str(tools / "run_real_failure_matrix.py"),
                "--output-dir",
                str(run_root / "failures"),
            ),
        ),
        (
            "features",
            (
                python,
                str(tools / "run_real_feature_workflows.py"),
                "--output-dir",
                str(run_root / "features"),
            ),
        ),
        (
            "routes",
            (
                python,
                str(tools / "run_real_v3_routes.py"),
                "--profiles",
                str(profiles),
                "--output-dir",
                str(run_root / "routes"),
            ),
        ),
        (
            "night",
            (
                python,
                str(tools / "run_real_night_matrix.py"),
                "--output-dir",
                str(run_root / "night"),
            ),
        ),
        (
            "browser",
            (
                python,
                str(tools / "run_browser_acceptance.py"),
                "--output-dir",
                str(run_root / "browser"),
            ),
        ),
        (
            "report",
            (
                python,
                str(tools / "build_full_acceptance_report.py"),
                "--run-dir",
                str(run_root),
            ),
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    run_root = args.output_dir / (args.run_id or f"full-{stamp}")
    run_root.mkdir(parents=True, exist_ok=False)
    backend_root = Path(__file__).resolve().parents[1]
    repository = backend_root.parents[1]
    start_provenance = workspace_fingerprint(repository)
    stage_records: list[dict[str, object]] = []

    def execute(name: str, command: tuple[str, ...]) -> int:
        if name == "report":
            end_provenance = workspace_fingerprint(repository)
            provenance = {
                **start_provenance,
                "workspace_end_fingerprint": end_provenance[
                    "workspace_fingerprint"
                ],
                "workspace_stable": end_provenance == start_provenance,
            }
            (run_root / "provenance.json").write_text(
                json.dumps(provenance, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if not provenance["workspace_stable"]:
                return 2
        result = subprocess.run(
            command,
            cwd=backend_root,
            text=True,
            capture_output=True,
            check=False,
        )
        (run_root / f"{name}.stdout.log").write_text(
            result.stdout, encoding="utf-8"
        )
        (run_root / f"{name}.stderr.log").write_text(
            result.stderr, encoding="utf-8"
        )
        stage_records.append(
            {"name": name, "command": list(command), "exit_code": result.returncode}
        )
        (run_root / "stages.json").write_text(
            json.dumps(stage_records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return result.returncode

    run_stages(_default_stages(backend_root, run_root), executor=execute)
    final_provenance = workspace_fingerprint(repository)
    if final_provenance != start_provenance:
        raise SystemExit("workspace changed while full acceptance was running")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
