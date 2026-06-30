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


if __name__ == "__main__":
    unittest.main()
