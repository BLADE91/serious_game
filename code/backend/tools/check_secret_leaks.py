from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
from typing import Iterable


@dataclass(frozen=True, slots=True)
class SecretPattern:
    pattern_id: str
    expression: re.Pattern[bytes]


PATTERNS = (
    SecretPattern("openai_style_key", re.compile(rb"sk-[A-Za-z0-9]{20,}")),
    SecretPattern(
        "bearer_credential",
        re.compile(rb"(?i)authorization\s*[:=]\s*bearer\s+[A-Za-z0-9._]{24,}"),
    ),
    SecretPattern(
        "literal_api_credential",
        re.compile(
            rb"(?i)(?:api[_-]?key|access[_-]?token)\s*[:=]\s*['\"]?"
            rb"[A-Za-z0-9]{24,}"
        ),
    ),
)


def _tracked_files(repo_root: Path) -> tuple[Path, ...]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    paths = completed.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    return tuple(
        repo_root / path for path in paths if path and (repo_root / path).is_file()
    )


def _evidence_files(evidence_root: Path) -> tuple[Path, ...]:
    if not evidence_root.exists():
        return ()
    return tuple(path for path in evidence_root.rglob("*") if path.is_file())


def find_secret_material(paths: Iterable[Path]) -> tuple[tuple[Path, str], ...]:
    findings: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        payload = resolved.read_bytes()
        for pattern in PATTERNS:
            if pattern.expression.search(payload):
                findings.append((resolved, pattern.pattern_id))
    return tuple(findings)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan tracked sources and one acceptance evidence tree for secrets."
    )
    parser.add_argument("repo_root", type=Path)
    parser.add_argument("--evidence-root", required=True, type=Path)
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    evidence_root = args.evidence_root.resolve()
    findings = find_secret_material(
        (*_tracked_files(repo_root), *_evidence_files(evidence_root))
    )
    if findings:
        for path, pattern_id in findings:
            try:
                display_path = path.relative_to(repo_root).as_posix()
            except ValueError:
                display_path = str(path)
            print(f"secret_material:{pattern_id}:{display_path}")
        return 1
    print("secret scan passed: no high-confidence credential material found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
