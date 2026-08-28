from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import Callable, Iterable


Stage = tuple[str, tuple[str, ...]]
StageExecutor = Callable[[str, tuple[str, ...]], int]


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
    stage_records: list[dict[str, object]] = []

    def execute(name: str, command: tuple[str, ...]) -> int:
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
