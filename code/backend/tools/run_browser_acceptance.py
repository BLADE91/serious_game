from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import tempfile


def _walk_tests(node: object) -> list[dict]:
    if isinstance(node, list):
        return [item for child in node for item in _walk_tests(child)]
    if not isinstance(node, dict):
        return []
    found: list[dict] = []
    for spec in node.get("specs", ()):
        if isinstance(spec, dict):
            found.extend(item for item in spec.get("tests", ()) if isinstance(item, dict))
    for suite in node.get("suites", ()):
        found.extend(_walk_tests(suite))
    return found


def validate_browser_report(report: dict, *, expected_tests: int) -> dict[str, int]:
    tests = _walk_tests(report)
    counts = {"total": len(tests), "passed": 0, "failed": 0, "skipped": 0}
    for test in tests:
        status = str(test.get("status", ""))
        if status == "expected":
            counts["passed"] += 1
        elif status == "skipped":
            counts["skipped"] += 1
        else:
            counts["failed"] += 1
    if counts["total"] < expected_tests:
        raise AssertionError(
            f"browser acceptance executed {counts['total']} tests; expected at least {expected_tests}"
        )
    if counts["skipped"]:
        raise AssertionError("browser acceptance contains skipped tests")
    if counts["failed"] or counts["passed"] != counts["total"]:
        raise AssertionError(f"browser acceptance did not fully pass: {counts}")
    return counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    backend_root = Path(__file__).resolve().parents[1]
    web_root = backend_root.parent / "frontend" / "web"
    profiles = json.loads((
        backend_root / "content" / "packages" / "pkg_gameplay_v3"
        / "acceptance_route_profiles.json"
    ).read_text(encoding="utf-8"))["profiles"]
    shard_count = max(1, min(4, int(os.getenv("FULL_E2E_SHARDS", "4"))))
    shard_root = output / "shards"
    shard_root.mkdir()

    def run_shard(shard_index: int) -> tuple[int, dict[str, int], Path]:
        shard_output = shard_root / f"shard-{shard_index + 1:02d}"
        shard_output.mkdir()
        environment = os.environ.copy()
        environment.update({
            "RUN_FULL_REAL_E2E": "1",
            "FULL_ACCEPTANCE_BROWSER_DIR": str(shard_output),
            "FULL_E2E_STORAGE_STATE": str(
                Path(tempfile.gettempdir())
                / f"qingjiang-e2e-auth-{os.getpid()}-{shard_index}.json"
            ),
            "FULL_E2E_SHARD_INDEX": str(shard_index),
            "FULL_E2E_SHARD_TOTAL": str(shard_count),
            "FULL_E2E_PYTHON": os.sys.executable,
        })
        completed = subprocess.run(
            ["npx.cmd", "playwright", "test", "e2e/full-game.spec.ts"],
            cwd=web_root,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        (shard_output / "stdout.log").write_text(completed.stdout, encoding="utf-8")
        (shard_output / "stderr.log").write_text(completed.stderr, encoding="utf-8")
        if completed.returncode:
            raise RuntimeError(f"browser shard {shard_index + 1} failed")
        expected_routes = len([index for index in range(len(profiles)) if index % shard_count == shard_index])
        expected = expected_routes + (1 if shard_index == 0 else 0)
        report = json.loads((shard_output / "playwright-report.json").read_text(encoding="utf-8"))
        return shard_index, validate_browser_report(report, expected_tests=expected), shard_output

    with ThreadPoolExecutor(max_workers=shard_count) as executor:
        shard_results = sorted(executor.map(run_shard, range(shard_count)))

    manifest = output / "browser-state-manifest.jsonl"
    manifest.write_text("", encoding="utf-8")
    route_counts = {"total": 0, "passed": 0, "failed": 0, "skipped": 0}
    for _, counts, shard_output in shard_results:
        for key in route_counts:
            route_counts[key] += counts[key]
        shard_manifest = shard_output / "browser-state-manifest.jsonl"
        if shard_manifest.is_file():
            with manifest.open("a", encoding="utf-8") as target:
                target.write(shard_manifest.read_text(encoding="utf-8"))

    visual_environment = os.environ.copy()
    visual_environment.update({
        "RUN_FULL_REAL_E2E": "1",
        "FULL_ACCEPTANCE_BROWSER_DIR": str(output),
        "FULL_E2E_STORAGE_STATE": str(
            Path(tempfile.gettempdir()) / f"qingjiang-e2e-auth-{os.getpid()}-visual.json"
        ),
        "FULL_E2E_PYTHON": os.sys.executable,
    })
    visual = subprocess.run(
        ["npx.cmd", "playwright", "test", "e2e/visual-matrix.spec.ts"],
        cwd=web_root,
        env=visual_environment,
        text=True,
        capture_output=True,
        check=False,
    )
    (output / "stdout.log").write_text(visual.stdout, encoding="utf-8")
    (output / "stderr.log").write_text(visual.stderr, encoding="utf-8")
    if visual.returncode:
        raise SystemExit(visual.returncode)
    visual_report = json.loads((output / "playwright-report.json").read_text(encoding="utf-8"))
    visual_counts = validate_browser_report(visual_report, expected_tests=3)
    counts = {
        key: route_counts[key] + visual_counts[key]
        for key in route_counts
    }
    summary = {
        "status": "passed",
        "real_e2e_enabled": True,
        "route_profiles": len(profiles),
        "route_shards": shard_count,
        "viewports": 3,
        **counts,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
