from __future__ import annotations

import json
from pathlib import Path

from serious_game_backend.domain.errors import ContentValidationError
from serious_game_backend.infrastructure.script_packages.file_loader import (
    FileScriptPackageLoader,
)


def validate_published_package_identity(package_dir: Path) -> dict[str, object]:
    """Validate via the authoritative loader and expose hashes for provenance."""

    manifest = json.loads(
        (package_dir / "package_manifest.json").read_text(encoding="utf-8")
    )
    if manifest.get("status") != "published":
        raise ContentValidationError("真实验收只接受 published 剧本包")
    declared = str(manifest.get("content_hash", ""))
    package = FileScriptPackageLoader().load(package_dir)
    if package.content_hash != declared:
        raise ContentValidationError(
            "published 剧本包身份与 manifest 不一致",
            details={"declared": declared, "loaded": package.content_hash},
        )
    raw_hash = FileScriptPackageLoader.compute_content_hash(package_dir)
    return {
        "v3_manifest_hash": declared,
        # Compatibility alias for older evidence consumers; this is diagnostic only.
        "v3_computed_hash": raw_hash,
        "v3_raw_hash": raw_hash,
        "v3_portable_hash": FileScriptPackageLoader.compute_portable_content_hash(
            package_dir
        ),
        "v3_package_identity_verified": True,
    }
