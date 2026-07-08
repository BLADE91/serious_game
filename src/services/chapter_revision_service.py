"""章节式剧本的人工与 AI 辅助修订服务。"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import difflib
import hashlib
import json
from pathlib import Path
import re
import shutil
import threading
from typing import Any, Callable

from src.config import QwenConfig, load_dotenv
from src.generation.chapter_script_generator import ChapterScriptGenerator
from src.generation.pa_backend_script_client import PABackendScriptClient
from src.generation.qwen_client import ChatMessage, QwenChatClient
from src.services.revision_impact_analyzer import RevisionImpactAnalyzer
from src.services.source_snapshot_manager import SourceSnapshotManager


class ChapterRevisionService:
    """以 Markdown 为唯一创作源，重建受影响的章节式管线产物。"""

    TARGET_FILES = {
        "game_settings": "01_game_settings.md",
        "chapter_outline": "02_chapter_outline.md",
    }

    def __init__(
        self,
        outputs_dir: str | Path = "outputs/script_drafts",
        flash_client: QwenChatClient | None = None,
        pa_client: PABackendScriptClient | None = None,
        cancel_event: threading.Event | None = None,
        max_workers: int = 3,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> None:
        self._outputs_dir = Path(outputs_dir).resolve()
        self._flash_client = flash_client
        self._pa_client = pa_client
        self._cancel_event = cancel_event
        self._max_workers = max(1, min(max_workers, 3))
        self._progress_callback = progress_callback

    def load_source(self, base_version: str, target: str) -> dict[str, Any]:
        base_dir = self._resolve_base_dir(base_version)
        filename = self._target_filename(target)
        path = base_dir / filename
        if not path.exists():
            raise ValueError(f"修订目标不存在: {target}")
        content = path.read_text(encoding="utf-8")
        return {
            "base_version": self._relative_ref(base_dir),
            "target": target,
            "filename": filename,
            "content": content,
        }

    def load_sources(self, base_version: str) -> dict[str, Any]:
        """Load every editable Markdown source from a chapter version."""
        base_dir = self._resolve_base_dir(base_version)
        sources = []
        targets = ["game_settings", "chapter_outline"]
        targets.extend(
            path.stem.removeprefix("03_")
            for path in sorted(base_dir.glob("03_ch[0-9][0-9].md"))
        )
        for target in targets:
            filename = self._target_filename(target)
            path = base_dir / filename
            if path.exists():
                sources.append({
                    "target": target,
                    "filename": filename,
                    "content": path.read_text(encoding="utf-8"),
                })
        return {
            "base_version": self._relative_ref(base_dir),
            "sources": sources,
        }

    def apply_batch_revision(
        self,
        base_version: str,
        changed_sources: dict[str, str] | None = None,
        feedback: str = "",
        *,
        mode: str = "batch",
        original_sources: dict[str, str] | None = None,
        conservative_impact: bool = False,
    ) -> dict[str, Any]:
        """Create one revision from several source edits and rebuild everything."""
        base_dir = self._resolve_base_dir(base_version)
        changes = changed_sources or {}
        original_overrides = original_sources or {}
        if mode != "rebuild" and not changes:
            raise ValueError("批量修订至少需要一个已修改的源文件")
        for target, content in changes.items():
            self._target_filename(target)
            if not isinstance(content, str) or not content.strip():
                raise ValueError(f"{target} 的 Markdown 不能为空")

        revision_dir, revision_name = self._reserve_revision_dir(base_dir)
        self._copy_source_inputs(base_dir, revision_dir)
        original_contents: dict[str, str] = {}
        for target, content in changes.items():
            filename = self._target_filename(target)
            path = revision_dir / filename
            if not path.exists():
                raise ValueError(f"修订目标不存在: {target}")
            original_contents[target] = original_overrides.get(
                target,
                path.read_text(encoding="utf-8"),
            )
            self._atomic_write_text(path, content)

        chapters = {
            path.stem.removeprefix("03_"): path.read_text(encoding="utf-8")
            for path in sorted(revision_dir.glob("03_ch[0-9][0-9].md"))
        }
        impact_changes = {
            target: (original_contents[target], content)
            for target, content in changes.items()
            if original_contents[target] != content
        }
        analyzer = RevisionImpactAnalyzer()
        impact = (
            analyzer.conservative_external_impact(chapters)
            if conservative_impact
            else analyzer.analyze_batch(impact_changes, chapters)
        )
        impact["baseline_available"] = not conservative_impact
        self._write_json(revision_dir / "08_revision_impact.json", impact)

        manifest = {
            "revision": revision_name,
            "parent": self._relative_ref(base_dir),
            "mode": mode,
            "target": "batch",
            "feedback": feedback.strip(),
            "revision_engine": {
                "manual": "human",
                "ai": "pa_backend",
                "rebuild": "external_or_rebuild",
            }.get(mode, "human_or_external"),
            "sync_engine": "qwen_flash" if impact["affected_chapters"] else "none",
            "changed_files": [self._target_filename(target) for target in impact_changes],
            "impact": impact,
            "resolved_chapters": [],
            "unresolved_chapters": [],
            "blocking_changes": impact.get("blocking_changes", []),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "status": "building",
        }
        self._write_json(revision_dir / "revision_manifest.json", manifest)
        job = self._new_revision_job(revision_dir, impact)
        self._write_json(revision_dir / "revision_job.json", job)
        self._write_json(
            revision_dir / "revision_plan.json",
            self._build_revision_plan(revision_dir, impact),
        )

        try:
            result = self._rebuild_batch_revision(
                base_dir=base_dir,
                revision_dir=revision_dir,
                manifest=manifest,
                impact=impact,
                job=job,
            )
        except Exception as exc:
            manifest["status"] = "failed"
            job["status"] = "failed"
            self._write_json(revision_dir / "revision_manifest.json", manifest)
            self._write_json(revision_dir / "revision_job.json", job)
            raise RuntimeError(
                f"{self._relative_ref(revision_dir)}: {exc}"
            ) from exc
        return result

    def rebuild_from_sources(self, base_version: str) -> dict[str, Any]:
        """Rebuild a version after possible out-of-band Markdown edits."""
        base_dir = self._resolve_base_dir(base_version)
        detected, baseline_available = SourceSnapshotManager.diff(base_dir)
        changed_sources = {
            target: current
            for target, (_, current) in detected.items()
            if current.strip()
        }
        original_sources = {
            target: original
            for target, (original, _) in detected.items()
        }
        return self.apply_batch_revision(
            base_version,
            changed_sources,
            mode="rebuild",
            original_sources=original_sources,
            conservative_impact=not baseline_available,
        )

    def resume_batch_revision(self, revision_ref: str) -> dict[str, Any]:
        """Resume a failed batch revision without repeating completed tasks."""
        revision_dir = self._resolve_base_dir(revision_ref)
        manifest_path = revision_dir / "revision_manifest.json"
        job_path = revision_dir / "revision_job.json"
        impact_path = revision_dir / "08_revision_impact.json"
        if not (manifest_path.exists() and job_path.exists() and impact_path.exists()):
            raise ValueError("该版本不是可续跑的批量修订")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        job = json.loads(job_path.read_text(encoding="utf-8"))
        impact = json.loads(impact_path.read_text(encoding="utf-8"))
        base_dir = self._resolve_base_dir(manifest.get("parent", ""))
        incremental_impact = self._analyze_revision_delta(base_dir, revision_dir)
        impact = self._merge_impact_reports(impact, incremental_impact)
        manifest["impact"] = impact
        manifest["blocking_changes"] = impact.get("blocking_changes", [])
        manifest["sync_engine"] = (
            "qwen_flash" if impact.get("affected_chapters") else "none"
        )
        manifest["status"] = "building"
        job["status"] = "running"
        self._write_json(impact_path, impact)
        self._write_json(
            revision_dir / "revision_plan.json",
            self._build_revision_plan(revision_dir, impact),
        )
        self._write_json(manifest_path, manifest)
        self._write_json(job_path, job)
        try:
            return self._rebuild_batch_revision(
                base_dir=base_dir,
                revision_dir=revision_dir,
                manifest=manifest,
                impact=impact,
                job=job,
            )
        except Exception:
            manifest["status"] = "failed"
            job["status"] = "failed"
            self._write_json(manifest_path, manifest)
            self._write_json(job_path, job)
            raise

    def preview_manual(
        self,
        base_version: str,
        target: str,
        content: str,
    ) -> dict[str, Any]:
        source = self.load_source(base_version, target)
        if not content.strip():
            raise ValueError("修订后的 Markdown 不能为空")
        return self._preview_payload(source, content, mode="manual")

    def preview_ai(
        self,
        base_version: str,
        target: str,
        feedback: str,
        current_content: str = "",
        draft_sources: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if not feedback.strip():
            raise ValueError("AI 修订反馈不能为空")
        source = self.load_source(base_version, target)
        if current_content.strip():
            source["content"] = current_content
        base_dir = self._resolve_base_dir(base_version)
        revision_scope = self._build_ai_revision_scope(
            base_dir=base_dir,
            target=target,
            current_content=source["content"],
            feedback=feedback,
            draft_sources=draft_sources or {},
        )
        messages = revision_scope["messages"]
        pa_client = self._pa()
        pa_client.reset_conversation()
        revised = pa_client.complete(
            messages,
            temperature=0.2,
        )
        revised = self._clean_markdown(revised)
        if not revised.strip():
            raise ValueError("AI 返回了空的 Markdown")
        if revision_scope["mode"] == "block":
            revised = self._replace_content_range(
                source["content"],
                revision_scope["start"],
                revision_scope["end"],
                revised,
            )
        payload = self._preview_payload(source, revised, mode="ai")
        payload["feedback"] = feedback.strip()
        return payload

    def analyze_impact(
        self,
        base_version: str,
        target: str,
        content: str,
    ) -> dict[str, Any]:
        if not content.strip():
            raise ValueError("修订后的 Markdown 不能为空")
        source = self.load_source(base_version, target)
        base_dir = self._resolve_base_dir(base_version)
        chapters = {
            path.stem.removeprefix("03_"): path.read_text(encoding="utf-8")
            for path in sorted(base_dir.glob("03_ch[0-9][0-9].md"))
        }
        return RevisionImpactAnalyzer().analyze(
            target=target,
            original_content=source["content"],
            revised_content=content,
            chapters=chapters,
        )

    def apply_revision(
        self,
        base_version: str,
        target: str,
        content: str,
        mode: str,
        feedback: str = "",
        chapter_actions: dict[str, str] | None = None,
        impact_acknowledged: bool = False,
    ) -> dict[str, Any]:
        if mode not in {"manual", "ai"}:
            raise ValueError("mode 必须是 manual 或 ai")
        if not content.strip():
            raise ValueError("修订后的 Markdown 不能为空")

        base_dir = self._resolve_base_dir(base_version)
        filename = self._target_filename(target)
        if not (base_dir / filename).exists():
            raise ValueError(f"修订目标不存在: {target}")

        impact = self.analyze_impact(base_version, target, content)
        if impact["requires_confirmation"] and not impact_acknowledged:
            raise ValueError("该修订会影响其他章节，请先确认影响范围和处理方式")
        return self.apply_batch_revision(
            base_version,
            {target: content},
            feedback,
            mode=mode,
        )

    def _rebuild_batch_revision(
        self,
        base_dir: Path,
        revision_dir: Path,
        manifest: dict[str, Any],
        impact: dict[str, Any],
        job: dict[str, Any],
    ) -> dict[str, Any]:
        """Run resumable source synchronization, repair, extraction and validation."""
        job["status"] = "running"
        self._write_json(revision_dir / "revision_job.json", job)
        generator = ChapterScriptGenerator(
            flash_client=self._flash(),
            cancel_event=self._cancel_event,
        )
        generator._output_dir = revision_dir

        resolved = self._sync_batch_chapters(
            revision_dir, impact, manifest, job,
        )
        manifest["resolved_chapters"] = sorted(resolved)

        sources = self._read_source_files(revision_dir)
        continuity_task = self._job_task(job, "continuity_repair")
        continuity_path = revision_dir / "05_continuity_review.json"
        current_source_hash = SourceSnapshotManager.source_hash(revision_dir)
        continuity_reusable = (
            continuity_task.get("source_hash") == current_source_hash
            and self._can_reuse_job_task(
                job,
                "continuity_repair",
                str(continuity_task.get("input_hash") or ""),
                continuity_path,
            )
        )
        if continuity_reusable:
            continuity = json.loads(continuity_path.read_text(encoding="utf-8"))
        else:
            self._start_job_task(
                revision_dir,
                job,
                "continuity_repair",
                current_source_hash,
            )
            try:
                continuity, sources = generator.review_and_repair_sources(sources)
                for filename, content in sources.items():
                    self._atomic_write_text(revision_dir / filename, content)
                self._write_json(continuity_path, continuity)
                self._complete_job_task(
                    revision_dir,
                    job,
                    "continuity_repair",
                    continuity_path,
                    {"source_hash": SourceSnapshotManager.source_hash(revision_dir)},
                )
                for fix in continuity.get("applied_fixes", []):
                    filename = fix.get("file")
                    if filename and filename not in manifest["changed_files"]:
                        manifest["changed_files"].append(filename)
            except Exception as exc:
                self._fail_job_task(revision_dir, job, "continuity_repair", exc)
                raise

        source_paths = sorted(revision_dir.glob("03_ch[0-9][0-9].md"))
        if not source_paths:
            raise ValueError("批量修订版本缺少章节 Markdown")
        settings_md = (revision_dir / "01_game_settings.md").read_text(encoding="utf-8")
        outline_md = (revision_dir / "02_chapter_outline.md").read_text(encoding="utf-8")
        chapters_md = [path.read_text(encoding="utf-8") for path in source_paths]

        merge_input_hash = SourceSnapshotManager.source_hash(revision_dir)
        self._start_job_task(revision_dir, job, "merge_markdown", merge_input_hash)
        merged_md = generator._merge_chapters(settings_md, outline_md, chapters_md)
        merged_path = revision_dir / "04_merged.md"
        self._atomic_write_text(merged_path, merged_md)
        self._complete_job_task(revision_dir, job, "merge_markdown", merged_path)

        chapter_ids = [f"ch{index:02d}" for index in range(1, len(chapters_md) + 1)]
        global_json = self._extract_batch_global(
            generator, revision_dir, merged_md, chapter_ids, job,
        )
        chapter_jsons = self._extract_batch_chapters(
            generator, revision_dir, chapters_md, job,
        )
        generator._normalize_extracted_structure(global_json, chapter_jsons)
        merged_json = dict(global_json)
        merged_json.pop("_chapter_ids", None)
        merged_json["chapters"] = chapter_jsons
        structure_path = revision_dir / "06_script_structure.json"
        self._write_json(structure_path, merged_json)

        expected_npc_count = self._expected_npc_count(revision_dir)
        expected_decision_count = self._expected_decision_point_count(revision_dir)
        expected_ending_count = self._expected_ending_count(revision_dir)
        validation_input_hash = self._content_hash(
            merged_json,
            merged_md,
            continuity,
            expected_npc_count,
            expected_decision_count,
            expected_ending_count,
            4,
        )
        self._start_job_task(
            revision_dir,
            job,
            "validate",
            validation_input_hash,
        )
        validation = generator._call6_validate(
            merged_json,
            expected_npc_count=(
                expected_npc_count
                if expected_npc_count is not None
                else len(merged_json.get("npcs", []))
            ),
            expected_decision_point_count=expected_decision_count,
            expected_ending_count=expected_ending_count,
            complete_script_md=merged_md,
            continuity_review=continuity,
        )
        validation_path = revision_dir / "07_validation_report.json"
        self._write_json(validation_path, validation)
        self._complete_job_task(revision_dir, job, "validate", validation_path)
        merged_json["_validation"] = validation
        script = generator._build_script_design(merged_json)

        base_payload = self._load_latest_result(base_dir)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        final_path = revision_dir / f"final_{timestamp}.md"
        self._atomic_write_text(final_path, merged_md)
        needs_review = bool(
            impact.get("blocking_changes")
            or continuity.get("status") != "pass"
            or not validation.get("valid")
        )
        payload = {
            "script": self._jsonable(script),
            "full_md": merged_md,
            "contexts_used": base_payload.get("contexts_used", []),
            "rewritten_queries": base_payload.get("rewritten_queries", []),
            "generation_notes": [
                f"批量修订 {revision_dir.name}",
                "源 Markdown 已统一同步、连续性修复并全量重建 JSON。",
            ],
            "original_query": base_payload.get("original_query", ""),
            "feedback": manifest.get("feedback", ""),
            "revision_round": self._revision_number(revision_dir.name),
            "generation_mode": "chapter",
            "revision_status": "review_required" if needs_review else "complete",
        }
        result_path = revision_dir / f"script_revise_{timestamp}.json"
        self._write_json(result_path, payload)
        payload["saved_as"] = self._relative_ref(result_path)
        payload["revision_dir"] = self._relative_ref(revision_dir)

        manifest["status"] = payload["revision_status"]
        manifest["validation"] = validation
        manifest["continuity"] = continuity
        job["status"] = "complete"
        job["completed_at"] = datetime.now().isoformat(timespec="seconds")
        SourceSnapshotManager.capture(revision_dir)
        self._write_json(revision_dir / "revision_manifest.json", manifest)
        self._write_json(revision_dir / "revision_job.json", job)
        payload["revision_manifest"] = manifest
        return payload

    def _sync_batch_chapters(
        self,
        revision_dir: Path,
        impact: dict[str, Any],
        manifest: dict[str, Any],
        job: dict[str, Any],
    ) -> set[str]:
        affected = {
            item["chapter_id"]: item.get("reasons", [])
            for item in impact.get("affected_chapters", [])
            if isinstance(item, dict) and item.get("chapter_id")
        }
        settings_md = (revision_dir / "01_game_settings.md").read_text(encoding="utf-8")
        outline_md = (revision_dir / "02_chapter_outline.md").read_text(encoding="utf-8")
        resolved: set[str] = set()
        pending: dict[Any, tuple[str, Path, str]] = {}

        def revise(chapter_id: str, path: Path, reasons: list[str]) -> str:
            self._ensure_not_cancelled()
            messages = self._build_chapter_sync_messages(
                chapter_id=chapter_id,
                current_content=path.read_text(encoding="utf-8"),
                settings_md=settings_md,
                outline_md=outline_md,
                reasons=reasons,
            )
            value = self._flash().complete(
                messages,
                temperature=0.2,
                response_format="text",
                max_tokens=8192,
            )
            value = self._clean_markdown(value)
            if not value.strip():
                raise ValueError(f"同步修订 {chapter_id} 时返回了空内容")
            return value

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            for chapter_id, reasons in sorted(affected.items()):
                task_id = f"sync_{chapter_id}"
                path = revision_dir / f"03_{chapter_id}.md"
                input_hash = self._content_hash(settings_md, outline_md, reasons)
                if self._can_reuse_job_task(job, task_id, input_hash, path):
                    resolved.add(chapter_id)
                    continue
                if not path.exists():
                    continue
                self._start_job_task(revision_dir, job, task_id, input_hash)
                future = executor.submit(revise, chapter_id, path, reasons)
                pending[future] = (chapter_id, path, task_id)

            failures: list[Exception] = []
            for future in as_completed(pending):
                chapter_id, path, task_id = pending[future]
                try:
                    content = future.result()
                    self._atomic_write_text(path, content)
                    self._complete_job_task(revision_dir, job, task_id, path)
                    resolved.add(chapter_id)
                    if path.name not in manifest["changed_files"]:
                        manifest["changed_files"].append(path.name)
                except Exception as exc:
                    self._fail_job_task(revision_dir, job, task_id, exc)
                    failures.append(exc)
            if failures:
                raise failures[0]
        return resolved

    def _analyze_revision_delta(
        self,
        base_dir: Path,
        revision_dir: Path,
    ) -> dict[str, Any]:
        changes: dict[str, tuple[str, str]] = {}
        filenames = {path.name for path in SourceSnapshotManager.source_paths(base_dir)}
        filenames.update(path.name for path in SourceSnapshotManager.source_paths(revision_dir))
        for filename in sorted(filenames):
            base_path = base_dir / filename
            revision_path = revision_dir / filename
            original = base_path.read_text(encoding="utf-8") if base_path.exists() else ""
            current = revision_path.read_text(encoding="utf-8") if revision_path.exists() else ""
            if original != current:
                changes[self.target_for_filename(filename)] = (original, current)
        chapters = {
            path.stem.removeprefix("03_"): path.read_text(encoding="utf-8")
            for path in sorted(revision_dir.glob("03_ch[0-9][0-9].md"))
        }
        return RevisionImpactAnalyzer().analyze_batch(changes, chapters)

    @staticmethod
    def _merge_impact_reports(*reports: dict[str, Any]) -> dict[str, Any]:
        rank = {"low": 0, "medium": 1, "high": 2}
        level = "low"
        reasons: dict[str, list[str]] = {}
        changed_targets: set[str] = set()
        structural_changes: list[str] = []
        blocking_changes: list[str] = []
        baseline_available = True
        for report in reports:
            report_level = report.get("impact_level", "low")
            if rank.get(report_level, 0) > rank[level]:
                level = report_level
            changed_targets.update(report.get("changed_targets", []))
            baseline_available = baseline_available and report.get(
                "baseline_available", True,
            )
            for item in report.get("affected_chapters", []):
                chapter_id = item.get("chapter_id")
                if not chapter_id:
                    continue
                chapter_reasons = reasons.setdefault(chapter_id, [])
                for reason in item.get("reasons", []):
                    if reason not in chapter_reasons:
                        chapter_reasons.append(reason)
            for source, destination in (
                (report.get("structural_changes", []), structural_changes),
                (report.get("blocking_changes", []), blocking_changes),
            ):
                for value in source:
                    if value not in destination:
                        destination.append(value)
        return {
            "target": "batch",
            "changed_targets": sorted(changed_targets),
            "impact_level": level,
            "affected_chapters": [
                {"chapter_id": chapter_id, "reasons": chapter_reasons}
                for chapter_id, chapter_reasons in sorted(reasons.items())
            ],
            "structural_changes": structural_changes,
            "blocking_changes": blocking_changes,
            "requires_confirmation": False,
            "baseline_available": baseline_available,
        }

    def _extract_batch_global(
        self,
        generator: ChapterScriptGenerator,
        revision_dir: Path,
        merged_md: str,
        chapter_ids: list[str],
        job: dict[str, Any],
    ) -> dict[str, Any]:
        task_id = "extract_global"
        path = revision_dir / "06a_global.json"
        input_hash = self._content_hash(merged_md, chapter_ids)
        if self._can_reuse_job_task(job, task_id, input_hash, path):
            return json.loads(path.read_text(encoding="utf-8"))
        self._start_job_task(revision_dir, job, task_id, input_hash)
        try:
            value = generator._call5a_extract_global(merged_md, chapter_ids)
            self._write_json(path, value)
            self._complete_job_task(revision_dir, job, task_id, path)
            return value
        except Exception as exc:
            self._fail_job_task(revision_dir, job, task_id, exc)
            raise

    def _extract_batch_chapters(
        self,
        generator: ChapterScriptGenerator,
        revision_dir: Path,
        chapters_md: list[str],
        job: dict[str, Any],
    ) -> list[dict[str, Any]]:
        results: dict[int, dict[str, Any]] = {}
        pending: dict[Any, tuple[int, str, Path, str]] = {}
        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            for index, chapter_md in enumerate(chapters_md, start=1):
                chapter_id = f"ch{index:02d}"
                task_id = f"extract_{chapter_id}"
                path = revision_dir / f"06b_{chapter_id}.json"
                input_hash = self._content_hash(chapter_id, chapter_md)
                if self._can_reuse_job_task(job, task_id, input_hash, path):
                    results[index] = json.loads(path.read_text(encoding="utf-8"))
                    continue
                self._start_job_task(revision_dir, job, task_id, input_hash)
                future = executor.submit(
                    generator._call5b_extract_chapter,
                    chapter_md,
                    chapter_id,
                    index,
                )
                pending[future] = (index, chapter_id, path, task_id)

            failures: list[Exception] = []
            for future in as_completed(pending):
                index, _, path, task_id = pending[future]
                try:
                    value = future.result()
                    self._write_json(path, value)
                    self._complete_job_task(revision_dir, job, task_id, path)
                    results[index] = value
                except Exception as exc:
                    self._fail_job_task(revision_dir, job, task_id, exc)
                    failures.append(exc)
            if failures:
                raise failures[0]
        return [results[index] for index in range(1, len(chapters_md) + 1)]

    @staticmethod
    def _build_chapter_sync_messages(
        chapter_id: str,
        current_content: str,
        settings_md: str,
        outline_md: str,
        reasons: list[str],
    ) -> list[ChatMessage]:
        reasons_text = "\n".join(f"- {reason}" for reason in reasons)
        return [
            ChatMessage(
                role="system",
                content=(
                    "你是章节式剧本同步修订器。只修复上游设定或大纲变化对当前章节造成的影响，"
                    "保留不受影响的剧情、ID、选项结构和文字。输出完整章节 Markdown，不要解释。"
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"## 章节\n{chapter_id}\n\n"
                    f"## 影响原因\n{reasons_text}\n\n"
                    f"## 最新全局设定\n{settings_md}\n\n"
                    f"## 最新章节大纲\n{outline_md}\n\n"
                    f"## 当前章节 Markdown\n{current_content}\n\n"
                    "根据影响原因同步修订当前章节，并直接输出完整 Markdown。"
                ),
            ),
        ]

    def _expected_decision_point_count(self, revision_dir: Path) -> int | None:
        generation_dir = self._generation_dir(revision_dir)
        path = generation_dir / "00_generation_request.json"
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8")).get(
                "decision_point_count"
            )
        except (json.JSONDecodeError, OSError, AttributeError):
            return None
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def _expected_npc_count(self, revision_dir: Path) -> int | None:
        generation_dir = self._generation_dir(revision_dir)
        path = generation_dir / "00_generation_request.json"
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8")).get("npc_count")
        except (json.JSONDecodeError, OSError, AttributeError):
            return None
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def _expected_ending_count(self, revision_dir: Path) -> int | None:
        generation_dir = self._generation_dir(revision_dir)
        path = generation_dir / "00_generation_request.json"
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8")).get("ending_count")
        except (json.JSONDecodeError, OSError, AttributeError):
            return None
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def _build_ai_revision_messages(
        self,
        base_dir: Path,
        target: str,
        current_content: str,
        feedback: str,
        draft_sources: dict[str, str] | None = None,
    ) -> list[ChatMessage]:
        draft_sources = draft_sources or {}
        context_parts = []
        included_targets: set[str] = set()
        for draft_target, draft_content in draft_sources.items():
            if draft_target == target or not isinstance(draft_content, str) or not draft_content.strip():
                continue
            filename = self._target_filename(draft_target)
            context_parts.append(f"## {filename}（当前批量草稿）\n{draft_content}")
            included_targets.add(draft_target)
        for filename in ("01_game_settings.md", "02_chapter_outline.md"):
            path = base_dir / filename
            context_target = self.target_for_filename(filename)
            if (
                path.exists()
                and path.name != self._target_filename(target)
                and context_target not in included_targets
            ):
                context_parts.append(f"## {filename}\n{path.read_text(encoding='utf-8')}")
        context = "\n\n".join(context_parts)
        return [
            ChatMessage(
                role="system",
                content=(
                    "你是章节式剧本 Markdown 定向修订器。只修改用户指定的目标文件，"
                    "保留未被反馈要求修改的内容、ID、结构和数值。"
                    "如果反馈中包含元素 ID，只修改该 ID 所属的章节、节点、选项、NPC 或结局内容块，"
                    "目标块之外的 Markdown 必须原样保留。只输出修改后的完整 Markdown。"
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"修订目标: {target}\n修订反馈: {feedback.strip()}\n\n"
                    f"## 约束上下文\n{context}\n\n"
                    f"## 当前目标文件\n{current_content}\n\n"
                    "直接输出修改后的完整目标文件，不要解释，不要代码块。"
                ),
            ),
        ]

    def _build_ai_revision_scope(
        self,
        base_dir: Path,
        target: str,
        current_content: str,
        feedback: str,
        draft_sources: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        marker = self._extract_revision_marker(feedback)
        if marker:
            block = self._find_markdown_block(current_content, marker)
            if not block:
                raise ValueError(f"源文件中找不到元素 {marker}")
            return {
                "mode": "block",
                "start": block["start"],
                "end": block["end"],
                "messages": self._build_ai_block_revision_messages(
                    base_dir=base_dir,
                    target=target,
                    marker=marker,
                    block_content=block["text"],
                    feedback=feedback,
                    draft_sources=draft_sources or {},
                ),
            }

        if re.fullmatch(r"ch\d{2}", target) and len(current_content) > 30000:
            raise ValueError(
                "长章节 AI 修订必须使用元素级修订入口，避免把完整章节发送给 PA Backend"
            )

        return {
            "mode": "full",
            "messages": self._build_ai_revision_messages(
                base_dir=base_dir,
                target=target,
                current_content=current_content,
                feedback=feedback,
                draft_sources=draft_sources or {},
            ),
        }

    def _build_ai_block_revision_messages(
        self,
        base_dir: Path,
        target: str,
        marker: str,
        block_content: str,
        feedback: str,
        draft_sources: dict[str, str] | None = None,
    ) -> list[ChatMessage]:
        context = self._build_compact_revision_context(
            base_dir, target, draft_sources or {},
        )
        return [
            ChatMessage(
                role="system",
                content=(
                    "你是章节式剧本 Markdown 局部修订器。只修改用户指定的 Markdown 内容块，"
                    "保留该块内未被反馈要求修改的 ID、结构、数值和格式。"
                    "不要输出完整文件，不要解释，不要代码块。"
                ),
            ),
            ChatMessage(
                role="user",
                content=(
                    f"修订目标: {target}\n"
                    f"元素 ID: {marker}\n"
                    f"修订反馈: {feedback.strip()}\n\n"
                    f"## 压缩约束上下文\n{context}\n\n"
                    f"## 当前 Markdown 内容块\n{block_content}\n\n"
                    "直接输出修订后的完整内容块。不得输出目标块之外的 Markdown。"
                ),
            ),
        ]

    def _build_compact_revision_context(
        self,
        base_dir: Path,
        target: str,
        draft_sources: dict[str, str],
    ) -> str:
        parts: list[str] = []
        for draft_target, draft_content in draft_sources.items():
            if draft_target == target or not isinstance(draft_content, str) or not draft_content.strip():
                continue
            filename = self._target_filename(draft_target)
            parts.append(f"## {filename}（当前批量草稿摘要）\n{self._summarize_revision_context(draft_content)}")
        for filename in ("01_game_settings.md", "02_chapter_outline.md"):
            path = base_dir / filename
            context_target = self.target_for_filename(filename)
            if not path.exists() or context_target == target or context_target in draft_sources:
                continue
            parts.append(f"## {filename}（摘要）\n{self._summarize_revision_context(path.read_text(encoding='utf-8'))}")
        return "\n\n".join(parts) or "无额外上下文。"

    @staticmethod
    def _summarize_revision_context(content: str, limit: int = 6000) -> str:
        if len(content) <= limit:
            return content
        head = content[: int(limit * 0.65)]
        tail = content[-int(limit * 0.30):]
        return head + "\n\n<!-- 中间长上下文已省略 -->\n\n" + tail

    @staticmethod
    def _extract_revision_marker(feedback: str) -> str:
        match = re.search(r"仅修订元素\s+(.+?)\s+所属内容块", feedback)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _find_markdown_block(content: str, marker: str) -> dict[str, Any] | None:
        marker_index = content.find(marker)
        if marker_index < 0:
            return None
        headings = [
            {"index": match.start(), "level": len(match.group(1))}
            for match in re.finditer(r"(?m)^(#{1,6})\s+.+$", content)
        ]
        start_heading = None
        for heading in headings:
            if heading["index"] <= marker_index:
                start_heading = heading
            else:
                break
        if start_heading is None:
            line_start = content.rfind("\n", 0, marker_index) + 1
            line_end = content.find("\n", marker_index)
            end = line_end + 1 if line_end >= 0 else len(content)
            return {"start": line_start, "end": end, "text": content[line_start:end]}
        end = len(content)
        for heading in headings:
            if heading["index"] > start_heading["index"] and heading["level"] <= start_heading["level"]:
                end = heading["index"]
                break
        return {
            "start": start_heading["index"],
            "end": end,
            "text": content[start_heading["index"]:end].rstrip(),
        }

    @staticmethod
    def _replace_content_range(
        content: str,
        start: int,
        end: int,
        replacement: str,
    ) -> str:
        separator = "\n\n" if end < len(content) else "\n"
        return content[:start] + replacement.rstrip() + separator + content[end:]

    @classmethod
    def target_for_filename(cls, filename: str) -> str:
        for target, target_filename in cls.TARGET_FILES.items():
            if target_filename == filename:
                return target
        match = re.fullmatch(r"03_(ch\d{2})\.md", filename)
        if match:
            return match.group(1)
        raise ValueError(f"不支持的源文件: {filename}")

    def _flash(self) -> QwenChatClient:
        if self._flash_client is None:
            load_dotenv(override=False)
            config = QwenConfig.from_env()
            self._flash_client = QwenChatClient(QwenConfig(
                api_key=config.api_key,
                base_url=config.base_url,
                model="qwen-flash",
                timeout_seconds=config.timeout_seconds,
            ))
        return self._flash_client

    def _pa(self) -> PABackendScriptClient:
        if self._pa_client is None:
            load_dotenv(override=False)
            self._pa_client = PABackendScriptClient(
                cancel_event=self._cancel_event,
            )
        return self._pa_client

    def _resolve_base_dir(self, base_version: str) -> Path:
        raw = base_version.strip().strip("/")
        if not raw:
            raise ValueError("base_version 不能为空")
        path = (self._outputs_dir / raw).resolve()
        try:
            path.relative_to(self._outputs_dir)
        except ValueError as exc:
            raise ValueError("非法的版本路径") from exc
        if path.is_file():
            path = path.parent
        if not path.is_dir():
            raise ValueError(f"版本目录不存在: {base_version}")
        if not (path / "01_game_settings.md").exists():
            raise ValueError("该版本不是可修订的章节式版本")
        return path

    @classmethod
    def _target_filename(cls, target: str) -> str:
        if target in cls.TARGET_FILES:
            return cls.TARGET_FILES[target]
        if re.fullmatch(r"ch\d{2}", target):
            return f"03_{target}.md"
        raise ValueError("target 必须是 game_settings、chapter_outline 或 chNN")

    def _reserve_revision_dir(self, base_dir: Path) -> tuple[Path, str]:
        generation_dir = self._generation_dir(base_dir)
        root = generation_dir / "revisions"
        root.mkdir(parents=True, exist_ok=True)
        numbers = [
            self._revision_number(path.name)
            for path in root.iterdir()
            if path.is_dir() and re.fullmatch(r"r\d{2}", path.name)
        ]
        name = f"r{(max(numbers, default=0) + 1):02d}"
        path = root / name
        path.mkdir()
        return path, name

    def _generation_dir(self, base_dir: Path) -> Path:
        """Return the owning vNN directory for an original or revised version."""
        relative = base_dir.resolve().relative_to(self._outputs_dir)
        if not relative.parts or not re.fullmatch(r"v\d{2}", relative.parts[0]):
            raise ValueError("章节修订版本必须位于 vNN 目录中")
        generation_dir = self._outputs_dir / relative.parts[0]
        if not generation_dir.is_dir():
            raise ValueError("找不到修订版本所属的生成目录")
        return generation_dir

    @staticmethod
    def _revision_number(value: str) -> int:
        match = re.fullmatch(r"r(\d{2})", value)
        return int(match.group(1)) if match else 0

    @staticmethod
    def _copy_source_inputs(base_dir: Path, revision_dir: Path) -> None:
        filenames = ["01_game_settings.md", "02_chapter_outline.md"]
        filenames.extend(
            path.name for path in sorted(base_dir.glob("03_ch[0-9][0-9].md"))
        )
        for filename in filenames:
            source = base_dir / filename
            if source.exists():
                shutil.copy2(source, revision_dir / filename)

    @staticmethod
    def _read_source_files(directory: Path) -> dict[str, str]:
        paths = [directory / "01_game_settings.md", directory / "02_chapter_outline.md"]
        paths.extend(sorted(directory.glob("03_ch[0-9][0-9].md")))
        return {
            path.name: path.read_text(encoding="utf-8")
            for path in paths
            if path.exists()
        }

    def _new_revision_job(
        self,
        revision_dir: Path,
        impact: dict[str, Any],
    ) -> dict[str, Any]:
        chapter_ids = [
            path.stem.removeprefix("03_")
            for path in sorted(revision_dir.glob("03_ch[0-9][0-9].md"))
        ]
        sync_ids = {
            item.get("chapter_id")
            for item in impact.get("affected_chapters", [])
            if isinstance(item, dict)
        }
        task_ids = [f"sync_{chapter_id}" for chapter_id in chapter_ids if chapter_id in sync_ids]
        task_ids.extend(["continuity_repair", "merge_markdown", "extract_global"])
        task_ids.extend(f"extract_{chapter_id}" for chapter_id in chapter_ids)
        task_ids.append("validate")
        return {
            "job_version": 1,
            "status": "pending",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "max_workers": self._max_workers,
            "tasks": {
                task_id: {
                    "status": "pending",
                    "attempts": 0,
                    "input_hash": "",
                    "output_file": "",
                    "output_hash": "",
                    "error": "",
                }
                for task_id in task_ids
            },
        }

    def _build_revision_plan(
        self,
        revision_dir: Path,
        impact: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "plan_version": 1,
            "orchestrator": "deterministic",
            "source_files": sorted(self._read_source_files(revision_dir)),
            "impact": impact,
            "execution_order": [
                "sync affected chapter Markdown with bounded parallelism",
                "review and repair continuity",
                "merge repaired Markdown",
                "extract global and chapter JSON with bounded parallelism",
                "validate and publish result",
            ],
            "max_workers": self._max_workers,
        }

    @staticmethod
    def _job_task(job: dict[str, Any], task_id: str) -> dict[str, Any]:
        return job.setdefault("tasks", {}).setdefault(task_id, {
            "status": "pending",
            "attempts": 0,
            "input_hash": "",
            "output_file": "",
            "output_hash": "",
            "error": "",
        })

    def _start_job_task(
        self,
        revision_dir: Path,
        job: dict[str, Any],
        task_id: str,
        input_hash: str = "",
    ) -> None:
        self._ensure_not_cancelled()
        task = self._job_task(job, task_id)
        task["status"] = "running"
        task["attempts"] = int(task.get("attempts", 0)) + 1
        task["input_hash"] = input_hash
        task["error"] = ""
        self._write_json(revision_dir / "revision_job.json", job)
        self._report_job_progress(job, f"开始 {task_id}")

    def _complete_job_task(
        self,
        revision_dir: Path,
        job: dict[str, Any],
        task_id: str,
        output_path: Path,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        task = self._job_task(job, task_id)
        task["status"] = "complete"
        task["output_file"] = output_path.name
        task["output_hash"] = hashlib.sha256(output_path.read_bytes()).hexdigest()
        task["error"] = ""
        if metadata:
            task.update(metadata)
        self._write_json(revision_dir / "revision_job.json", job)
        self._report_job_progress(job, f"完成 {task_id}")

    def _can_reuse_job_task(
        self,
        job: dict[str, Any],
        task_id: str,
        input_hash: str,
        output_path: Path,
    ) -> bool:
        task = self._job_task(job, task_id)
        if task.get("status") != "complete" or task.get("input_hash") != input_hash:
            return False
        if not output_path.exists():
            return False
        expected = str(task.get("output_hash") or "")
        return bool(expected) and expected == hashlib.sha256(output_path.read_bytes()).hexdigest()

    @staticmethod
    def _content_hash(*values: Any) -> str:
        payload = json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _fail_job_task(
        self,
        revision_dir: Path,
        job: dict[str, Any],
        task_id: str,
        error: Exception,
    ) -> None:
        task = self._job_task(job, task_id)
        task["status"] = "failed"
        task["error"] = str(error)
        self._write_json(revision_dir / "revision_job.json", job)
        self._report_job_progress(job, f"失败 {task_id}")

    def _ensure_not_cancelled(self) -> None:
        if self._cancel_event and self._cancel_event.is_set():
            raise RuntimeError("批量修订已被用户取消")

    def _report_job_progress(self, job: dict[str, Any], name: str) -> None:
        if self._progress_callback is None:
            return
        tasks = list(job.get("tasks", {}).values())
        completed = len([task for task in tasks if task.get("status") == "complete"])
        self._progress_callback(completed, max(1, len(tasks)), name)

    def _preview_payload(
        self,
        source: dict[str, Any],
        revised: str,
        mode: str,
    ) -> dict[str, Any]:
        diff = "\n".join(difflib.unified_diff(
            source["content"].splitlines(),
            revised.splitlines(),
            fromfile=f"{source['target']}:before",
            tofile=f"{source['target']}:after",
            lineterm="",
        ))
        return {
            **source,
            "mode": mode,
            "revised_content": revised,
            "diff": diff,
            "changed": source["content"] != revised,
        }

    def _load_latest_result(self, base_dir: Path) -> dict[str, Any]:
        candidates = sorted(
            list(base_dir.glob("script_generate_*.json"))
            + list(base_dir.glob("script_revise_*.json")),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            return {}
        try:
            return json.loads(candidates[0].read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def _relative_ref(self, path: Path) -> str:
        return path.resolve().relative_to(self._outputs_dir).as_posix()

    @staticmethod
    def _clean_markdown(value: str) -> str:
        cleaned = value.strip()
        if cleaned.startswith("```") and cleaned.endswith("```"):
            cleaned = cleaned[3:-3].strip()
            if cleaned.lower().startswith("markdown"):
                cleaned = cleaned[8:].strip()
            elif cleaned.lower().startswith("md"):
                cleaned = cleaned[2:].strip()
        return cleaned + "\n"

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        ChapterRevisionService._atomic_write_text(
            path,
            json.dumps(value, ensure_ascii=False, indent=2),
        )

    @staticmethod
    def _atomic_write_text(path: Path, value: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
        temp_path.write_text(value, encoding="utf-8")
        temp_path.replace(path)

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if is_dataclass(value):
            return {key: ChapterRevisionService._jsonable(item) for key, item in asdict(value).items()}
        if isinstance(value, dict):
            return {str(key): ChapterRevisionService._jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [ChapterRevisionService._jsonable(item) for item in value]
        if isinstance(value, set):
            return sorted(ChapterRevisionService._jsonable(item) for item in value)
        return value
