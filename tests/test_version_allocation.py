"""Tests for generation version allocation."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.api import server


class TestVersionAllocation(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
