"""Tests for chapter Markdown revision and incremental rebuilds."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from src.services.chapter_revision_service import ChapterRevisionService
from src.services.revision_impact_analyzer import RevisionImpactAnalyzer
from src.generation.chapter_script_generator import ChapterScriptGenerator
from src.services.source_snapshot_manager import SourceSnapshotManager


class FakeFlashClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.reset_count = 0

    def reset_conversation(self) -> None:
        self.reset_count += 1

    def complete(self, messages, **kwargs) -> str:
        self.calls.append({"messages": messages, **kwargs})
        if not self.responses:
            raise AssertionError("Unexpected model call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class TestChapterRevisionService(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.outputs_dir = Path(self.temp_dir.name) / "script_drafts"
        self.base_dir = self.outputs_dir / "v01"
        fixture_dir = Path(__file__).resolve().parents[1] / "outputs" / "script_drafts" / "v01"
        shutil.copytree(fixture_dir, self.base_dir)
        self.original_chapter = (self.base_dir / "03_ch01.md").read_text(encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_manual_preview_does_not_write_files(self) -> None:
        service = ChapterRevisionService(outputs_dir=self.outputs_dir)
        revised = self.original_chapter + "\n人工修订标记。\n"

        preview = service.preview_manual("v01", "ch01", revised)

        self.assertTrue(preview["changed"])
        self.assertIn("+人工修订标记。", preview["diff"])
        self.assertFalse((self.base_dir / "revisions").exists())

    def test_apply_manual_revision_preserves_base_and_rebuilds_all_json(self) -> None:
        global_json = (self.base_dir / "06a_global.json").read_text(encoding="utf-8")
        chapter_jsons = [
            path.read_text(encoding="utf-8")
            for path in sorted(self.base_dir.glob("06b_ch[0-9][0-9].json"))
        ]
        flash = FakeFlashClient([
            json.dumps({
                "status": "pass", "continuity_issues": [], "patches": [],
            }, ensure_ascii=False),
            global_json,
            *chapter_jsons,
        ])
        service = ChapterRevisionService(
            outputs_dir=self.outputs_dir,
            flash_client=flash,
            max_workers=1,
        )
        revised = self.original_chapter + "\n人工修订标记。\n"

        result = service.apply_revision("v01", "ch01", revised, "manual")

        revision_dir = self.outputs_dir / result["revision_dir"]
        self.assertEqual(self.original_chapter, (self.base_dir / "03_ch01.md").read_text(encoding="utf-8"))
        self.assertEqual(revised, (revision_dir / "03_ch01.md").read_text(encoding="utf-8"))
        self.assertEqual(len(chapter_jsons), len(list(revision_dir.glob("06b_ch*.json"))))
        self.assertEqual(2 + len(chapter_jsons), len(flash.calls))
        self.assertTrue(result["script"]["validation_report"]["valid"])
        manifest = json.loads((revision_dir / "revision_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("complete", manifest["status"])
        self.assertEqual("v01", manifest["parent"])
        self.assertEqual("human", manifest["revision_engine"])
        self.assertEqual("none", manifest["sync_engine"])

    def test_revision_of_revision_uses_next_sibling_directory(self) -> None:
        service = ChapterRevisionService(outputs_dir=self.outputs_dir)
        first_dir, first_name = service._reserve_revision_dir(self.base_dir)
        second_dir, second_name = service._reserve_revision_dir(first_dir)

        self.assertEqual("r01", first_name)
        self.assertEqual("r02", second_name)
        self.assertEqual((self.base_dir / "revisions" / "r02").resolve(), second_dir)

    def test_ai_preview_returns_candidate_without_writing(self) -> None:
        candidate = self.original_chapter.replace("压力传导", "压力化解", 1)
        flash = FakeFlashClient([candidate])
        service = ChapterRevisionService(
            outputs_dir=self.outputs_dir,
            pa_client=flash,
        )

        preview = service.preview_ai("v01", "ch01", "调整章节标题")

        self.assertEqual("ai", preview["mode"])
        self.assertIn("压力化解", preview["revised_content"])
        self.assertTrue(preview["changed"])
        self.assertFalse((self.base_dir / "revisions").exists())
        self.assertEqual(1, flash.reset_count)
        self.assertNotIn("response_format", flash.calls[0])
        self.assertIn("调整章节标题", flash.calls[0]["messages"][1].content)

    def test_ai_preview_uses_other_changed_drafts_as_context(self) -> None:
        candidate = self.original_chapter.replace("压力传导", "压力化解", 1)
        pa = FakeFlashClient([candidate])
        service = ChapterRevisionService(
            outputs_dir=self.outputs_dir,
            pa_client=pa,
        )

        service.preview_ai(
            "v01",
            "ch01",
            "按新设定调整",
            current_content=self.original_chapter,
            draft_sources={
                "game_settings": "# 当前批量草稿中的新全局设定",
            },
        )

        prompt = pa.calls[0]["messages"][1].content
        self.assertIn("当前批量草稿中的新全局设定", prompt)
        self.assertIn("01_game_settings.md（当前批量草稿）", prompt)

    def test_settings_npc_change_only_marks_referencing_chapters(self) -> None:
        original = """## 角色表
### NPC 01：甲
- npc_id: npc_01
- 姓名: 甲
"""
        revised = original.replace("姓名: 甲", "姓名: 新甲")
        impact = RevisionImpactAnalyzer().analyze(
            "game_settings",
            original,
            revised,
            {
                "ch01": "- npc_id: npc_01",
                "ch02": "- npc_id: npc_02",
            },
        )

        self.assertEqual("medium", impact["impact_level"])
        self.assertEqual(
            ["ch01"],
            [item["chapter_id"] for item in impact["affected_chapters"]],
        )

    def test_outline_change_marks_changed_and_following_chapters(self) -> None:
        original = """# 大纲
## 第 1 章：一
- chapter_id: ch01
- core_task: 一
## 第 2 章：二
- chapter_id: ch02
- core_task: 二
## 第 3 章：三
- chapter_id: ch03
- core_task: 三
"""
        revised = original.replace("core_task: 二", "core_task: 新二")
        impact = RevisionImpactAnalyzer().analyze(
            "chapter_outline",
            original,
            revised,
            {"ch01": "一", "ch02": "二", "ch03": "三"},
        )

        self.assertEqual(
            ["ch02", "ch03"],
            [item["chapter_id"] for item in impact["affected_chapters"]],
        )

    def test_upstream_revision_requires_acknowledgement(self) -> None:
        service = ChapterRevisionService(outputs_dir=self.outputs_dir)
        original = (self.base_dir / "01_game_settings.md").read_text(encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "请先确认影响范围"):
            service.apply_revision(
                "v01",
                "game_settings",
                original + "\n全局约束发生变化。\n",
                "manual",
            )

    def test_upstream_single_revision_automatically_syncs_affected_chapters(self) -> None:
        original = (self.base_dir / "01_game_settings.md").read_text(encoding="utf-8")
        global_json = (self.base_dir / "06a_global.json").read_text(encoding="utf-8")
        chapters = [
            path.read_text(encoding="utf-8")
            for path in sorted(self.base_dir.glob("03_ch[0-9][0-9].md"))
        ]
        chapter_jsons = [
            path.read_text(encoding="utf-8")
            for path in sorted(self.base_dir.glob("06b_ch[0-9][0-9].json"))
        ]
        flash = FakeFlashClient([
            *chapters,
            json.dumps({
                "status": "pass", "continuity_issues": [], "patches": [],
            }, ensure_ascii=False),
            global_json,
            *chapter_jsons,
        ])
        service = ChapterRevisionService(
            outputs_dir=self.outputs_dir,
            flash_client=flash,
            max_workers=1,
        )

        result = service.apply_revision(
            "v01",
            "game_settings",
            original + "\n新增全局制作约束。\n",
            "manual",
            impact_acknowledged=True,
        )

        self.assertEqual("complete", result["revision_status"])
        manifest = result["revision_manifest"]
        self.assertEqual(
            [f"ch{index:02d}" for index in range(1, len(chapters) + 1)],
            manifest["resolved_chapters"],
        )
        self.assertFalse(manifest["unresolved_chapters"])
        revision_dir = self.outputs_dir / result["revision_dir"]
        self.assertTrue((revision_dir / "08_revision_impact.json").exists())

    def test_batch_revision_rebuilds_all_json_from_sources(self) -> None:
        global_json = (self.base_dir / "06a_global.json").read_text(encoding="utf-8")
        chapter_jsons = [
            path.read_text(encoding="utf-8")
            for path in sorted(self.base_dir.glob("06b_ch[0-9][0-9].json"))
        ]
        flash = FakeFlashClient([
            json.dumps({
                "status": "pass",
                "continuity_issues": [],
                "patches": [],
            }, ensure_ascii=False),
            global_json,
            *chapter_jsons,
        ])
        progress = []
        service = ChapterRevisionService(
            outputs_dir=self.outputs_dir,
            flash_client=flash,
            max_workers=1,
            progress_callback=lambda stage, total, name: progress.append(
                (stage, total, name)
            ),
        )
        revised = self.original_chapter + "\n批量修订标记。\n"

        result = service.apply_batch_revision(
            "v01",
            {"ch01": revised},
        )

        revision_dir = self.outputs_dir / result["revision_dir"]
        self.assertEqual(revised, (revision_dir / "03_ch01.md").read_text(encoding="utf-8"))
        self.assertTrue((revision_dir / "04_merged.md").exists())
        self.assertTrue((revision_dir / "05_continuity_review.json").exists())
        self.assertTrue((revision_dir / "06a_global.json").exists())
        self.assertEqual(len(chapter_jsons), len(list(revision_dir.glob("06b_ch*.json"))))
        self.assertTrue((revision_dir / "revision_job.json").exists())
        self.assertEqual("complete", result["revision_status"])
        self.assertEqual(2 + len(chapter_jsons), len(flash.calls))
        self.assertTrue(progress)
        self.assertEqual(progress[-1][0], progress[-1][1])

    def test_continuity_repair_applies_unique_patch_then_rechecks(self) -> None:
        flash = FakeFlashClient([
            json.dumps({
                "status": "repair_required",
                "continuity_issues": [{"code": "CONTINUITY_001"}],
                "patches": [{
                    "issue_code": "CONTINUITY_001",
                    "file": "03_ch02.md",
                    "old_text": "第二章发生在第3天",
                    "new_text": "第二章发生在第4天",
                    "reason": "与第一章结束时间一致",
                }],
            }, ensure_ascii=False),
            json.dumps({
                "status": "pass",
                "continuity_issues": [],
                "patches": [],
            }, ensure_ascii=False),
        ])
        generator = ChapterScriptGenerator(flash_client=flash)

        report, sources = generator.review_and_repair_sources({
            "01_game_settings.md": "全局设定",
            "02_chapter_outline.md": "章节大纲",
            "03_ch01.md": "第一章结束于第3天",
            "03_ch02.md": "第二章发生在第3天",
        })

        self.assertEqual("pass", report["status"])
        self.assertEqual("第二章发生在第4天", sources["03_ch02.md"])
        self.assertEqual(1, len(report["applied_fixes"]))

    def test_batch_impact_merges_upstream_changes(self) -> None:
        analyzer = RevisionImpactAnalyzer()
        impact = analyzer.analyze_batch({
            "game_settings": (
                "## 规则\n旧规则",
                "## 规则\n新规则",
            ),
            "ch01": ("旧章节", "新章节"),
        }, {
            "ch01": "第一章",
            "ch02": "第二章",
        })

        self.assertEqual(["ch01", "game_settings"], impact["changed_targets"])
        self.assertEqual("high", impact["impact_level"])
        self.assertEqual(
            ["ch01", "ch02"],
            [item["chapter_id"] for item in impact["affected_chapters"]],
        )

    def test_source_snapshot_detects_external_markdown_change(self) -> None:
        SourceSnapshotManager.capture(self.base_dir)
        settings_path = self.base_dir / "01_game_settings.md"
        original = settings_path.read_text(encoding="utf-8")
        settings_path.write_text(original + "\n外部修改。\n", encoding="utf-8")

        changes, available = SourceSnapshotManager.diff(self.base_dir)

        self.assertTrue(available)
        self.assertEqual(original, changes["game_settings"][0])
        self.assertIn("外部修改", changes["game_settings"][1])

    def test_source_snapshot_repairs_incomplete_and_stale_baseline(self) -> None:
        baseline_dir = self.base_dir / SourceSnapshotManager.BASELINE_DIR
        baseline_dir.mkdir()
        (baseline_dir / "01_game_settings.md").write_text("partial", encoding="utf-8")
        (baseline_dir / "03_ch99.md").write_text("stale", encoding="utf-8")

        captured = SourceSnapshotManager.capture_if_missing(self.base_dir)

        self.assertIsNotNone(captured)
        self.assertFalse((baseline_dir / "03_ch99.md").exists())
        self.assertEqual(
            {path.name for path in SourceSnapshotManager.source_paths(self.base_dir)},
            {path.name for path in baseline_dir.glob("*.md")},
        )

    def test_external_rebuild_uses_snapshot_for_impact_analysis(self) -> None:
        SourceSnapshotManager.capture(self.base_dir)
        settings_path = self.base_dir / "01_game_settings.md"
        settings_path.write_text(
            settings_path.read_text(encoding="utf-8") + "\n外部全局约束。\n",
            encoding="utf-8",
        )
        service = ChapterRevisionService(outputs_dir=self.outputs_dir)
        service._rebuild_batch_revision = MagicMock(return_value={"ok": True})

        result = service.rebuild_from_sources("v01")

        self.assertEqual({"ok": True}, result)
        impact = json.loads(
            (self.base_dir / "revisions" / "r01" / "08_revision_impact.json")
            .read_text(encoding="utf-8")
        )
        self.assertTrue(impact["baseline_available"])
        self.assertEqual("high", impact["impact_level"])
        self.assertEqual(
            ["ch01", "ch02", "ch03", "ch04"],
            [item["chapter_id"] for item in impact["affected_chapters"]],
        )

    def test_revision_validation_uses_requested_npc_count(self) -> None:
        (self.base_dir / "00_generation_request.json").write_text(
            json.dumps({"npc_count": 9}, ensure_ascii=False),
            encoding="utf-8",
        )
        service = ChapterRevisionService(outputs_dir=self.outputs_dir)

        self.assertEqual(9, service._expected_npc_count(self.base_dir))

    def test_resume_reextracts_completed_chapter_when_source_changed(self) -> None:
        global_json = (self.base_dir / "06a_global.json").read_text(encoding="utf-8")
        chapter_jsons = [
            path.read_text(encoding="utf-8")
            for path in sorted(self.base_dir.glob("06b_ch[0-9][0-9].json"))
        ]
        continuity_pass = json.dumps({
            "status": "pass",
            "continuity_issues": [],
            "patches": [],
        }, ensure_ascii=False)
        first_flash = FakeFlashClient([
            continuity_pass,
            global_json,
            chapter_jsons[0],
            RuntimeError("temporary extraction failure"),
            *chapter_jsons[2:],
        ])
        service = ChapterRevisionService(
            outputs_dir=self.outputs_dir,
            flash_client=first_flash,
            max_workers=1,
        )

        with self.assertRaisesRegex(RuntimeError, "temporary extraction failure"):
            service.apply_batch_revision(
                "v01",
                {"ch01": self.original_chapter + "\n第一轮修改。\n"},
            )

        revision_dir = self.base_dir / "revisions" / "r01"
        chapter_path = revision_dir / "03_ch01.md"
        chapter_path.write_text(
            chapter_path.read_text(encoding="utf-8") + "\n失败后人工修改。\n",
            encoding="utf-8",
        )
        resume_flash = FakeFlashClient([
            continuity_pass,
            global_json,
            chapter_jsons[0],
            chapter_jsons[1],
        ])
        resumed = ChapterRevisionService(
            outputs_dir=self.outputs_dir,
            flash_client=resume_flash,
            max_workers=1,
        ).resume_batch_revision("v01/revisions/r01")

        job = json.loads((revision_dir / "revision_job.json").read_text(encoding="utf-8"))
        self.assertEqual("complete", resumed["revision_status"])
        self.assertEqual(2, job["tasks"]["extract_ch01"]["attempts"])
        self.assertEqual(1, job["tasks"]["extract_ch03"]["attempts"])
        self.assertEqual(4, len(resume_flash.calls))

    def test_resume_expands_impact_after_failed_global_source_edit(self) -> None:
        service = ChapterRevisionService(outputs_dir=self.outputs_dir)
        service._rebuild_batch_revision = MagicMock(
            side_effect=RuntimeError("temporary failure")
        )
        with self.assertRaisesRegex(RuntimeError, "temporary failure"):
            service.apply_batch_revision(
                "v01",
                {"ch01": self.original_chapter + "\n第一轮修改。\n"},
            )

        revision_dir = self.base_dir / "revisions" / "r01"
        settings_path = revision_dir / "01_game_settings.md"
        settings_path.write_text(
            settings_path.read_text(encoding="utf-8") + "\n失败后全局修改。\n",
            encoding="utf-8",
        )
        resumed_service = ChapterRevisionService(outputs_dir=self.outputs_dir)
        resumed_service._rebuild_batch_revision = MagicMock(return_value={"ok": True})

        resumed_service.resume_batch_revision("v01/revisions/r01")

        impact = json.loads(
            (revision_dir / "08_revision_impact.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            ["ch01", "ch02", "ch03", "ch04"],
            [item["chapter_id"] for item in impact["affected_chapters"]],
        )


if __name__ == "__main__":
    unittest.main()
