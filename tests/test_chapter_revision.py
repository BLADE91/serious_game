"""Tests for chapter Markdown revision and incremental rebuilds."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import unittest

from src.services.chapter_revision_service import ChapterRevisionService
from src.services.revision_impact_analyzer import RevisionImpactAnalyzer


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
        return self.responses.pop(0)


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

    def test_apply_manual_revision_preserves_base_and_rebuilds_target(self) -> None:
        extracted_chapter = (self.base_dir / "06b_ch01.json").read_text(encoding="utf-8")
        flash = FakeFlashClient([
            json.dumps({"status": "pass", "continuity_issues": []}, ensure_ascii=False),
            extracted_chapter,
        ])
        service = ChapterRevisionService(
            outputs_dir=self.outputs_dir,
            flash_client=flash,
        )
        revised = self.original_chapter + "\n人工修订标记。\n"
        untouched_extraction = (self.base_dir / "06b_ch02.json").read_bytes()

        result = service.apply_revision("v01", "ch01", revised, "manual")

        revision_dir = self.outputs_dir / result["revision_dir"]
        self.assertEqual(self.original_chapter, (self.base_dir / "03_ch01.md").read_text(encoding="utf-8"))
        self.assertEqual(revised, (revision_dir / "03_ch01.md").read_text(encoding="utf-8"))
        self.assertEqual(untouched_extraction, (revision_dir / "06b_ch02.json").read_bytes())
        self.assertEqual(2, len(flash.calls))
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

    def test_kept_affected_chapters_are_marked_review_required(self) -> None:
        original = (self.base_dir / "01_game_settings.md").read_text(encoding="utf-8")
        global_json = (self.base_dir / "06a_global.json").read_text(encoding="utf-8")
        flash = FakeFlashClient([
            json.dumps({"status": "pass", "continuity_issues": []}, ensure_ascii=False),
            global_json,
        ])
        service = ChapterRevisionService(
            outputs_dir=self.outputs_dir,
            flash_client=flash,
        )

        result = service.apply_revision(
            "v01",
            "game_settings",
            original + "\n新增全局制作约束。\n",
            "manual",
            impact_acknowledged=True,
        )

        self.assertEqual("review_required", result["revision_status"])
        manifest = result["revision_manifest"]
        self.assertTrue(manifest["unresolved_chapters"])
        revision_dir = self.outputs_dir / result["revision_dir"]
        self.assertTrue((revision_dir / "08_revision_impact.json").exists())


if __name__ == "__main__":
    unittest.main()
