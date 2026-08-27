from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping


def build_content_manifest(root: str | Path) -> dict[str, Any]:
    content_root = Path(root).resolve()
    if not content_root.is_dir():
        raise FileNotFoundError(content_root)
    files: list[dict[str, Any]] = []
    for path in sorted(
        (candidate for candidate in content_root.rglob("*") if candidate.is_file()),
        key=lambda candidate: candidate.relative_to(content_root).as_posix(),
    ):
        payload = path.read_bytes()
        files.append({
            "path": path.relative_to(content_root).as_posix(),
            "sha256": sha256(payload).hexdigest(),
            "size": len(payload),
        })
    return {
        "schema_version": 1,
        "root_name": content_root.name,
        "file_count": len(files),
        "total_bytes": sum(item["size"] for item in files),
        "files": files,
    }


def compare_content_manifest(
    current: Mapping[str, Any], baseline: Mapping[str, Any]
) -> tuple[str, ...]:
    differences: list[str] = []
    for field in ("schema_version", "root_name", "file_count", "total_bytes"):
        if current.get(field) != baseline.get(field):
            differences.append(
                f"{field}: expected {baseline.get(field)!r}, got {current.get(field)!r}"
            )
    current_files = {
        str(item["path"]): item for item in current.get("files", [])
    }
    baseline_files = {
        str(item["path"]): item for item in baseline.get("files", [])
    }
    for path in sorted(baseline_files.keys() - current_files.keys()):
        differences.append(f"missing_file:{path}")
    for path in sorted(current_files.keys() - baseline_files.keys()):
        differences.append(f"unexpected_file:{path}")
    for path in sorted(current_files.keys() & baseline_files.keys()):
        for field in ("sha256", "size"):
            if current_files[path].get(field) != baseline_files[path].get(field):
                differences.append(f"changed_file:{path}:{field}")
    return tuple(differences)


def _write_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Hash every byte in a content tree.")
    parser.add_argument("path", type=Path)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--compare", type=Path)
    action.add_argument("--write", type=Path)
    args = parser.parse_args()

    manifest = build_content_manifest(args.path)
    if args.write:
        _write_manifest(args.write, manifest)
        print(f"wrote {manifest['file_count']} file hashes to {args.write}")
        return 0
    if args.compare:
        baseline = json.loads(args.compare.read_text(encoding="utf-8"))
        differences = compare_content_manifest(manifest, baseline)
        if differences:
            for difference in differences:
                print(difference)
            return 1
        print(
            f"content tree matches: {manifest['file_count']} files, "
            f"{manifest['total_bytes']} bytes"
        )
        return 0
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
