"""Tests for generation version allocation."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.api import server


class TestVersionAllocation(unittest.TestCase):
    @staticmethod
    def _route_endpoint(path: str):
        return next(route.endpoint for route in server.app.routes if route.path == path)

    def test_script_serializer_converts_sets_to_stable_lists(self) -> None:
        serialized = server.serialize_script({
            "active_flags": {"flag_b", "flag_a"},
            "nested": ({"node_2", "node_1"},),
        })

        self.assertEqual(["flag_a", "flag_b"], serialized["active_flags"])
        self.assertEqual([["node_1", "node_2"]], serialized["nested"])

    def test_relative_output_ref_uses_url_separators(self) -> None:
        root = Path("outputs") / "script_drafts"
        artifact = root / "v02" / "script_generate.json"

        self.assertEqual(
            "v02/script_generate.json",
            server._relative_output_ref(artifact, root),
        )

    def test_existing_directory_wins_over_stale_counter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs_dir = Path(temp_dir) / "script_drafts"
            outputs_dir.mkdir()
            (outputs_dir / "v01").mkdir()
            counter_file = outputs_dir / ".version_counter"
            counter_file.write_text('{"gen": 0, "rev": 0}', encoding="utf-8")

            with (
                patch.object(server, "OUTPUTS_DIR", outputs_dir),
                patch.object(server, "COUNTER_FILE", counter_file),
            ):
                version_dir, gen, rev = server._reserve_generation_version_dir()

            self.assertEqual(outputs_dir / "v02", version_dir)
            self.assertEqual((2, 0), (gen, rev))
            self.assertTrue(version_dir.is_dir())
            self.assertEqual(
                {"gen": 2, "rev": 0},
                json.loads(counter_file.read_text(encoding="utf-8")),
            )

    def test_version_apis_return_and_load_latest_completed_result(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs_dir = Path(temp_dir) / "script_drafts"
            v01 = outputs_dir / "v01"
            v02 = outputs_dir / "v02"
            v01.mkdir(parents=True)
            v02.mkdir()
            first = v01 / "script_generate_first.json"
            second = v02 / "script_generate_second.json"
            first.write_text(json.dumps({
                "script": {"title": "first", "chapters": []},
                "generation_mode": "chapter",
            }), encoding="utf-8")
            second.write_text(json.dumps({
                "script": {"title": "second", "chapters": []},
                "generation_mode": "chapter",
            }), encoding="utf-8")
            os.utime(first, (1, 1))
            os.utime(second, (2, 2))

            with patch.object(server, "OUTPUTS_DIR", outputs_dir):
                versions = asyncio.run(self._route_endpoint("/api/versions")())
                latest = asyncio.run(self._route_endpoint("/api/latest-result")())

            self.assertEqual(2, len(versions["versions"]))
            self.assertEqual(
                {"v01/script_generate_first.json", "v02/script_generate_second.json"},
                {item["filename"] for item in versions["versions"]},
            )
            self.assertEqual("v02/script_generate_second.json", latest["filename"])
            self.assertEqual("second", latest["script"]["title"])

    def test_incomplete_versions_are_listed_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs_dir = Path(temp_dir) / "script_drafts"
            v01 = outputs_dir / "v01"
            v02 = outputs_dir / "v02"
            v03 = outputs_dir / "v03"
            v01.mkdir(parents=True)
            v02.mkdir()
            v03.mkdir()
            (v01 / "script_generate_done.json").write_text("{}", encoding="utf-8")
            (v02 / "03_ch01.md").write_text("chapter 1", encoding="utf-8")
            (v02 / "03_ch02.md").write_text("chapter 2", encoding="utf-8")
            (v03 / "01_game_settings.md").write_text("settings", encoding="utf-8")

            self.assertEqual([v03, v02], server._incomplete_generation_dirs(outputs_dir))
            with patch.object(server, "OUTPUTS_DIR", outputs_dir):
                response = asyncio.run(self._route_endpoint("/api/incomplete-versions")())
            self.assertEqual(["v03", "v02"], [item["version"] for item in response["versions"]])
            self.assertEqual(2, response["versions"][1]["chapter_count"])
            self.assertFalse(response["versions"][1]["has_saved_request"])

            (v02 / "script_generate_done.json").write_text("{}", encoding="utf-8")
            (v03 / "script_generate_done.json").write_text("{}", encoding="utf-8")
            self.assertEqual([], server._incomplete_generation_dirs(outputs_dir))
            with patch.object(server, "OUTPUTS_DIR", outputs_dir):
                response = asyncio.run(self._route_endpoint("/api/incomplete-versions")())
            self.assertEqual([], response["versions"])

    def test_resume_uses_saved_request_or_infers_legacy_chapter_count(self) -> None:
        request = server.GenerateRequest(
            scenario="new scenario",
            player_role="new role",
            learning_goal="new goal",
            chapter_count=6,
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            version_dir = Path(temp_dir) / "v02"
            version_dir.mkdir()
            (version_dir / "03_ch01.md").write_text("1", encoding="utf-8")
            (version_dir / "03_ch08.md").write_text("8", encoding="utf-8")

            inferred = server._effective_generation_values(request, version_dir)
            self.assertEqual(8, inferred["chapter_count"])

            (version_dir / server.GENERATION_REQUEST_FILE).write_text(
                json.dumps({
                    "scenario": "saved scenario",
                    "player_role": "saved role",
                    "learning_goal": "saved goal",
                    "chapter_count": 4,
                }),
                encoding="utf-8",
            )
            saved = server._effective_generation_values(request, version_dir)
            self.assertEqual("saved scenario", saved["scenario"])
            self.assertEqual(4, saved["chapter_count"])


if __name__ == "__main__":
    unittest.main()
