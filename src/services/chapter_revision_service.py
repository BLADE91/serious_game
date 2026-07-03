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
from typing import Any

from src.config import QwenConfig, load_dotenv
from src.generation.chapter_script_generator import ChapterScriptGenerator
from src.generation.pa_backend_script_client import PABackendScriptClient
from src.generation.qwen_client import ChatMessage, QwenChatClient
from src.services.revision_impact_analyzer import RevisionImpactAnalyzer


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
    ) -> None:
        self._outputs_dir = Path(outputs_dir).resolve()
        self._flash_client = flash_client
        self._pa_client = pa_client
        self._cancel_event = cancel_event
        self._max_workers = max(1, min(max_workers, 3))

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
    ) -> dict[str, Any]:
        """Create one revision from several source edits and rebuild everything."""
        base_dir = self._resolve_base_dir(base_version)
        changes = changed_sources or {}
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
            original_contents[target] = path.read_text(encoding="utf-8")
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
        impact = RevisionImpactAnalyzer().analyze_batch(impact_changes, chapters)
        self._write_json(revision_dir / "08_revision_impact.json", impact)

        manifest = {
            "revision": revision_name,
            "parent": self._relative_ref(base_dir),
            "mode": "batch",
            "target": "batch",
            "feedback": feedback.strip(),
            "revision_engine": "human_or_external",
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
        manifest["status"] = "building"
        job["status"] = "running"
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
    ) -> dict[str, Any]:
        if not feedback.strip():
            raise ValueError("AI 修订反馈不能为空")
        source = self.load_source(base_version, target)
        if current_content.strip():
            source["content"] = current_content
        base_dir = self._resolve_base_dir(base_version)
        messages = self._build_ai_revision_messages(
            base_dir=base_dir,
            target=target,
            current_content=source["content"],
            feedback=feedback,
        )
        pa_client = self._pa()
        pa_client.reset_conversation()
        revised = pa_client.complete(
            messages,
            temperature=0.2,
        )
        revised = self._clean_markdown(revised)
        if not revised.strip():
            raise ValueError("AI 返回了空的 Markdown")
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
        actions = chapter_actions or {}
        allowed_actions = {"keep", "ai_revise"}
        invalid_actions = sorted(
            action for action in actions.values() if action not in allowed_actions
        )
        if invalid_actions:
            raise ValueError(f"不支持的章节处理方式: {invalid_actions}")
        if impact["requires_confirmation"] and not impact_acknowledged:
            raise ValueError("该修订会影响其他章节，请先确认影响范围和处理方式")
        affected_ids = {
            item.get("chapter_id")
            for item in impact.get("affected_chapters", [])
            if isinstance(item, dict) and item.get("chapter_id")
        }
        uses_sync_model = any(
            actions.get(chapter_id) == "ai_revise"
            for chapter_id in affected_ids
        )

        revision_dir, revision_name = self._reserve_revision_dir(base_dir)
        self._copy_revision_inputs(base_dir, revision_dir)
        (revision_dir / filename).write_text(content, encoding="utf-8")
        self._write_json(revision_dir / "08_revision_impact.json", impact)

        manifest = {
            "revision": revision_name,
            "parent": self._relative_ref(base_dir),
            "mode": mode,
            "target": target,
            "feedback": feedback.strip(),
            "revision_engine": "pa_backend" if mode == "ai" else "human",
            "sync_engine": "qwen_flash" if uses_sync_model else "none",
            "changed_files": [filename],
            "impact": impact,
            "chapter_actions": actions,
            "resolved_chapters": [],
            "unresolved_chapters": [],
            "blocking_changes": impact.get("blocking_changes", []),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "status": "building",
        }
        self._write_json(revision_dir / "revision_manifest.json", manifest)

        try:
            result = self._rebuild_revision(
                base_dir=base_dir,
                revision_dir=revision_dir,
                target=target,
                manifest=manifest,
                impact=impact,
                chapter_actions=actions,
            )
        except Exception:
            manifest["status"] = "failed"
            self._write_json(revision_dir / "revision_manifest.json", manifest)
            raise

        manifest["status"] = result["revision_status"]
        manifest["validation"] = result["script"].get("validation_report", {})
        self._write_json(revision_dir / "revision_manifest.json", manifest)
        result["revision_manifest"] = manifest
        return result

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
        if continuity_task["status"] == "complete" and continuity_path.exists():
            continuity = json.loads(continuity_path.read_text(encoding="utf-8"))
        else:
            self._start_job_task(revision_dir, job, "continuity_repair")
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

        self._start_job_task(revision_dir, job, "merge_markdown")
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
        merged_json = dict(global_json)
        merged_json.pop("_chapter_ids", None)
        merged_json["chapters"] = chapter_jsons
        structure_path = revision_dir / "06_script_structure.json"
        self._write_json(structure_path, merged_json)

        self._start_job_task(revision_dir, job, "validate")
        validation = generator._call6_validate(
            merged_json,
            expected_npc_count=len(merged_json.get("npcs", [])),
            expected_decision_point_count=self._expected_decision_point_count(revision_dir),
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
                if self._job_task(job, task_id)["status"] == "complete" and path.exists():
                    resolved.add(chapter_id)
                    continue
                if not path.exists():
                    continue
                self._start_job_task(revision_dir, job, task_id)
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
        if self._job_task(job, task_id)["status"] == "complete" and path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        self._start_job_task(revision_dir, job, task_id)
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
                if self._job_task(job, task_id)["status"] == "complete" and path.exists():
                    results[index] = json.loads(path.read_text(encoding="utf-8"))
                    continue
                self._start_job_task(revision_dir, job, task_id)
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

    def _rebuild_revision(
        self,
        base_dir: Path,
        revision_dir: Path,
        target: str,
        manifest: dict[str, Any],
        impact: dict[str, Any],
        chapter_actions: dict[str, str],
    ) -> dict[str, Any]:
        resolved_chapters, unresolved_chapters = self._apply_chapter_actions(
            revision_dir=revision_dir,
            impact=impact,
            chapter_actions=chapter_actions,
            manifest=manifest,
        )
        chapter_paths = sorted(revision_dir.glob("03_ch[0-9][0-9].md"))
        if not chapter_paths:
            raise ValueError("修订版本缺少章节 Markdown")

        settings_md = (revision_dir / "01_game_settings.md").read_text(encoding="utf-8")
        outline_md = (revision_dir / "02_chapter_outline.md").read_text(encoding="utf-8")
        chapters_md = [path.read_text(encoding="utf-8") for path in chapter_paths]

        generator = ChapterScriptGenerator(
            flash_client=self._flash(),
            cancel_event=self._cancel_event,
        )
        generator._output_dir = revision_dir
        merged_md = generator._merge_chapters(settings_md, outline_md, chapters_md)
        generator._save_intermediate("04_merged_before_review.md", merged_md)

        try:
            continuity = generator._call4_consistency_review(merged_md)
        except Exception as exc:
            continuity = {
                "status": "not_run",
                "continuity_issues": [],
                "error": str(exc),
            }
        generator._save_intermediate(
            "05_continuity_review.json",
            json.dumps(continuity, ensure_ascii=False, indent=2),
        )

        chapter_ids = [f"ch{index:02d}" for index in range(1, len(chapters_md) + 1)]
        global_path = revision_dir / "06a_global.json"
        if target in {"game_settings", "chapter_outline"} or not global_path.exists():
            global_json = generator._call5a_extract_global(merged_md, chapter_ids)
            self._write_json(global_path, global_json)
        else:
            global_json = json.loads(global_path.read_text(encoding="utf-8"))

        chapter_jsons: list[dict[str, Any]] = []
        for index, chapter_md in enumerate(chapters_md, start=1):
            chapter_id = f"ch{index:02d}"
            chapter_json_path = revision_dir / f"06b_{chapter_id}.json"
            if target == chapter_id or chapter_id in resolved_chapters or not chapter_json_path.exists():
                chapter_json = generator._call5b_extract_chapter(
                    chapter_md, chapter_id, index,
                )
                self._write_json(chapter_json_path, chapter_json)
            else:
                chapter_json = json.loads(chapter_json_path.read_text(encoding="utf-8"))
            chapter_jsons.append(chapter_json)

        merged_json = dict(global_json)
        merged_json.pop("_chapter_ids", None)
        merged_json["chapters"] = chapter_jsons
        self._write_json(revision_dir / "06_script_structure.json", merged_json)

        expected_npc_count = len(merged_json.get("npcs", []))
        validation = generator._call6_validate(
            merged_json,
            expected_npc_count=expected_npc_count,
            expected_decision_point_count=self._expected_decision_point_count(revision_dir),
            complete_script_md=merged_md,
            continuity_review=continuity,
        )
        self._write_json(revision_dir / "07_validation_report.json", validation)
        merged_json["_validation"] = validation
        script = generator._build_script_design(merged_json)

        base_payload = self._load_latest_result(base_dir)
        revision_round = self._revision_number(revision_dir.name)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        final_name = f"final_{timestamp}.md"
        result_name = f"script_revise_{timestamp}.json"
        (revision_dir / final_name).write_text(merged_md, encoding="utf-8")

        payload = {
            "script": self._jsonable(script),
            "full_md": merged_md,
            "contexts_used": base_payload.get("contexts_used", []),
            "rewritten_queries": base_payload.get("rewritten_queries", []),
            "generation_notes": [
                f"章节式修订 {revision_dir.name}: {target} ({manifest['mode']})",
                "Markdown 源文件已更新，并重新执行连续性、抽取和程序校验。",
            ],
            "original_query": base_payload.get("original_query", ""),
            "feedback": manifest.get("feedback", ""),
            "revision_round": revision_round,
            "generation_mode": "chapter",
        }
        continuity_status = continuity.get("status")
        needs_review = bool(
            unresolved_chapters
            or impact.get("blocking_changes")
            or not validation.get("valid")
            or continuity_status != "pass"
        )
        if needs_review:
            payload["generation_notes"].append(
                "存在尚未处理的上游影响、校验问题或连续性建议，请继续人工修订。"
            )
        payload["revision_status"] = "review_required" if needs_review else "complete"
        self._write_json(revision_dir / result_name, payload)
        payload["saved_as"] = self._relative_ref(revision_dir / result_name)
        payload["revision_dir"] = self._relative_ref(revision_dir)
        return payload

    def _apply_chapter_actions(
        self,
        revision_dir: Path,
        impact: dict[str, Any],
        chapter_actions: dict[str, str],
        manifest: dict[str, Any],
    ) -> tuple[set[str], list[str]]:
        affected = impact.get("affected_chapters", [])
        reason_map = {
            item["chapter_id"]: item.get("reasons", [])
            for item in affected
            if isinstance(item, dict) and item.get("chapter_id")
        }
        resolved: set[str] = set()
        unresolved: list[str] = []
        settings_md = (revision_dir / "01_game_settings.md").read_text(encoding="utf-8")
        outline_md = (revision_dir / "02_chapter_outline.md").read_text(encoding="utf-8")

        for chapter_id in sorted(reason_map):
            action = chapter_actions.get(chapter_id, "keep")
            chapter_path = revision_dir / f"03_{chapter_id}.md"
            if action == "ai_revise" and chapter_path.exists():
                current = chapter_path.read_text(encoding="utf-8")
                messages = self._build_chapter_sync_messages(
                    chapter_id=chapter_id,
                    current_content=current,
                    settings_md=settings_md,
                    outline_md=outline_md,
                    reasons=reason_map[chapter_id],
                )
                revised = self._flash().complete(
                    messages,
                    temperature=0.2,
                    response_format="text",
                    max_tokens=8192,
                )
                revised = self._clean_markdown(revised)
                if not revised.strip():
                    raise ValueError(f"AI 同步修订 {chapter_id} 时返回了空内容")
                chapter_path.write_text(revised, encoding="utf-8")
                resolved.add(chapter_id)
                if chapter_path.name not in manifest["changed_files"]:
                    manifest["changed_files"].append(chapter_path.name)
            else:
                unresolved.append(chapter_id)

        manifest["resolved_chapters"] = sorted(resolved)
        manifest["unresolved_chapters"] = unresolved
        return resolved, unresolved

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

    def _build_ai_revision_messages(
        self,
        base_dir: Path,
        target: str,
        current_content: str,
        feedback: str,
    ) -> list[ChatMessage]:
        context_parts = []
        for filename in ("01_game_settings.md", "02_chapter_outline.md"):
            path = base_dir / filename
            if path.exists() and path.name != self._target_filename(target):
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
    def _copy_revision_inputs(base_dir: Path, revision_dir: Path) -> None:
        filenames = [
            "01_game_settings.md",
            "02_chapter_outline.md",
            "06a_global.json",
        ]
        filenames.extend(path.name for path in sorted(base_dir.glob("03_ch[0-9][0-9].md")))
        filenames.extend(path.name for path in sorted(base_dir.glob("06b_ch[0-9][0-9].json")))
        for filename in filenames:
            source = base_dir / filename
            if source.exists():
                shutil.copy2(source, revision_dir / filename)

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
            "output_file": "",
            "output_hash": "",
            "error": "",
        })

    def _start_job_task(
        self,
        revision_dir: Path,
        job: dict[str, Any],
        task_id: str,
    ) -> None:
        task = self._job_task(job, task_id)
        task["status"] = "running"
        task["attempts"] = int(task.get("attempts", 0)) + 1
        task["error"] = ""
        self._write_json(revision_dir / "revision_job.json", job)

    def _complete_job_task(
        self,
        revision_dir: Path,
        job: dict[str, Any],
        task_id: str,
        output_path: Path,
    ) -> None:
        task = self._job_task(job, task_id)
        task["status"] = "complete"
        task["output_file"] = output_path.name
        task["output_hash"] = hashlib.sha256(output_path.read_bytes()).hexdigest()
        task["error"] = ""
        self._write_json(revision_dir / "revision_job.json", job)

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
