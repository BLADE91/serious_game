from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from serious_game_backend.domain.errors import ContentValidationError
from serious_game_backend.infrastructure.script_packages.file_loader import (
    FileScriptPackageLoader,
)
from tools.real_run_provenance import validate_published_package_identity


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = BACKEND_ROOT / "content" / "packages" / "pkg_gameplay_v3"


def _portable_checkout_copy(tmp_path: Path) -> Path:
    package_dir = tmp_path / "pkg_gameplay_v3"
    shutil.copytree(PACKAGE_ROOT, package_dir)
    for path in package_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".json", ".md"}:
            data = path.read_bytes().replace(b"\r\n", b"\n")
            path.write_bytes(data.replace(b"\n", b"\r\n"))
    return package_dir


def test_real_runner_provenance_accepts_loader_valid_portable_checkout(
    tmp_path: Path,
) -> None:
    package_dir = _portable_checkout_copy(tmp_path)
    declared = json.loads(
        (package_dir / "package_manifest.json").read_text(encoding="utf-8")
    )["content_hash"]
    raw = FileScriptPackageLoader.compute_content_hash(package_dir)
    assert raw != declared

    identity = validate_published_package_identity(package_dir)

    assert identity["v3_manifest_hash"] == declared
    assert identity["v3_raw_hash"] == raw
    assert identity["v3_package_identity_verified"] is True


def test_real_runner_provenance_rejects_substantive_tampering(tmp_path: Path) -> None:
    package_dir = _portable_checkout_copy(tmp_path)
    path = package_dir / "story_calendar.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["_tampered"] = True
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ContentValidationError):
        validate_published_package_identity(package_dir)
