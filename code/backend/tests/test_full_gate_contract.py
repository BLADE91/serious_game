from __future__ import annotations

import json
from pathlib import Path

from tools.check_secret_leaks import find_secret_material
from tools.hash_content_tree import build_content_manifest, compare_content_manifest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parents[1]
FULL_GATE = BACKEND_ROOT / "tools" / "run_full_test_gate.ps1"
SECRET_SCANNER = BACKEND_ROOT / "tools" / "check_secret_leaks.py"
V2_ROOT = BACKEND_ROOT / "content" / "packages" / "pkg_gameplay_v2"
V2_BASELINE = REPO_ROOT / "docs" / "testing" / "baselines" / "pkg_gameplay_v2.sha256.json"


def test_full_gate_contains_every_required_unfiltered_command() -> None:
    text = FULL_GATE.read_text(encoding="utf-8")
    for command in (
        "BEGIN.BAT --check",
        "python -m pytest -q",
        "npm test",
        "npm run build",
        "npm run lint",
        "hash_content_tree.py",
        "check_secret_leaks.py",
    ):
        assert command in text
    assert "-k " not in text
    assert "--test-name-pattern" not in text
    assert "--grep" not in text


def test_v2_byte_baseline_matches_every_current_file() -> None:
    baseline = json.loads(V2_BASELINE.read_text(encoding="utf-8"))
    current = build_content_manifest(V2_ROOT)

    assert compare_content_manifest(current, baseline) == ()
    assert current["file_count"] == len(current["files"])
    assert all(item["size"] >= 0 for item in current["files"])


def test_secret_scanner_uses_tracked_files_and_skips_local_env() -> None:
    text = SECRET_SCANNER.read_text(encoding="utf-8")

    assert "git" in text and "ls-files" in text
    assert "evidence-root" in text
    assert "rglob" in text
    assert "os.walk" not in text
    assert ".env" not in text


def test_secret_scanner_ignores_named_placeholders_but_finds_literal_keys(
    tmp_path: Path,
) -> None:
    placeholder = tmp_path / "placeholder.txt"
    actual = tmp_path / "actual.txt"
    placeholder.write_text(
        "api_key=sk-forbidden-example-value",
        encoding="utf-8",
    )
    actual.write_text("api_key=" + "A" * 32, encoding="utf-8")

    findings = find_secret_material((placeholder, actual))

    assert findings == ((actual.resolve(), "literal_api_credential"),)
