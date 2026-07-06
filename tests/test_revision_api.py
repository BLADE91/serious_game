"""API tests for resumable batch revision SSE."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api.server import create_app
from src.services.chapter_revision_service import ChapterRevisionService


class RevisionApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(create_app())

    def test_batch_run_streams_result_and_task_id(self) -> None:
        result = {
            "script": {},
            "full_md": "# rebuilt",
            "revision_status": "complete",
            "revision_dir": "v01/revisions/r99",
        }
        with patch.object(
            ChapterRevisionService,
            "apply_batch_revision",
            return_value=result,
        ):
            response = self.client.post("/api/revisions/run", json={
                "action": "apply",
                "base_version": "v01",
                "changed_sources": {"ch01": "# revised"},
            })

        self.assertEqual(200, response.status_code)
        self.assertTrue(response.headers.get("x-task-id"))
        self.assertIn("event: result", response.text)
        self.assertIn('"revision_status": "complete"', response.text)

    def test_batch_run_rejects_empty_apply(self) -> None:
        response = self.client.post("/api/revisions/run", json={
            "action": "apply",
            "base_version": "v01",
            "changed_sources": {},
        })

        self.assertEqual(400, response.status_code)
        self.assertIn("至少需要一个", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()

