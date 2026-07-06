"""Baseline snapshots for editable chapter-script Markdown sources."""

from __future__ import annotations

import hashlib
from pathlib import Path
import re
import shutil
from typing import Any


class SourceSnapshotManager:
    """Capture and compare source Markdown without depending on derived JSON."""

    BASELINE_DIR = ".source_baseline"
    FIXED_TARGETS = {
        "01_game_settings.md": "game_settings",
        "02_chapter_outline.md": "chapter_outline",
    }

    @classmethod
    def source_paths(cls, directory: Path) -> list[Path]:
        paths = [directory / filename for filename in cls.FIXED_TARGETS]
        paths.extend(sorted(directory.glob("03_ch[0-9][0-9].md")))
        return [path for path in paths if path.exists()]

    @classmethod
    def target_for_filename(cls, filename: str) -> str:
        if filename in cls.FIXED_TARGETS:
            return cls.FIXED_TARGETS[filename]
        match = re.fullmatch(r"03_(ch\d{2})\.md", filename)
        if match:
            return match.group(1)
        raise ValueError(f"不支持的源文件: {filename}")

    @classmethod
    def capture(cls, directory: Path) -> dict[str, Any]:
        baseline_dir = directory / cls.BASELINE_DIR
        baseline_dir.mkdir(parents=True, exist_ok=True)
        sources = cls.source_paths(directory)
        source_names = {path.name for path in sources}
        for stale_path in baseline_dir.glob("*.md"):
            if stale_path.name not in source_names:
                stale_path.unlink()
        files = {}
        for source in sources:
            destination = baseline_dir / source.name
            shutil.copy2(source, destination)
            files[source.name] = cls.hash_file(source)
        return {"baseline_dir": baseline_dir, "files": files}

    @classmethod
    def capture_if_missing(cls, directory: Path) -> dict[str, Any] | None:
        baseline_dir = directory / cls.BASELINE_DIR
        source_names = {path.name for path in cls.source_paths(directory)}
        baseline_names = (
            {path.name for path in baseline_dir.glob("*.md")}
            if baseline_dir.is_dir() else set()
        )
        if source_names and baseline_names == source_names:
            return None
        return cls.capture(directory)

    @classmethod
    def diff(cls, directory: Path) -> tuple[dict[str, tuple[str, str]], bool]:
        """Return target -> (baseline, current), plus whether a baseline exists."""
        baseline_dir = directory / cls.BASELINE_DIR
        if not baseline_dir.is_dir():
            return {}, False
        changes: dict[str, tuple[str, str]] = {}
        current_by_name = {path.name: path for path in cls.source_paths(directory)}
        baseline_by_name = {
            path.name: path
            for path in baseline_dir.glob("*.md")
            if path.is_file()
        }
        for filename in sorted(set(current_by_name) | set(baseline_by_name)):
            baseline = (
                baseline_by_name[filename].read_text(encoding="utf-8")
                if filename in baseline_by_name else ""
            )
            current = (
                current_by_name[filename].read_text(encoding="utf-8")
                if filename in current_by_name else ""
            )
            if baseline != current:
                changes[cls.target_for_filename(filename)] = (baseline, current)
        return changes, True

    @classmethod
    def source_hash(cls, directory: Path) -> str:
        digest = hashlib.sha256()
        for path in cls.source_paths(directory):
            digest.update(path.name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    @staticmethod
    def hash_file(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()
