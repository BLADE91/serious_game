from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from serious_game_backend.application.story_flow_service import StoryFlowService
from serious_game_backend.domain.errors import ContentValidationError
from serious_game_backend.infrastructure.script_packages.file_loader import (
    FileScriptPackageLoader,
)
from serious_game_backend.bootstrap import build_container
from serious_game_backend.config import Settings
from serious_game_backend.api.app import create_app


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = BACKEND_ROOT / "content" / "packages" / "pkg_gameplay_v3"


class StorySemanticsV3Tests(unittest.TestCase):
    def mutate_package(self, filename: str, mutate) -> Path:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        package_dir = Path(temporary.name) / "pkg_gameplay_v3"
        shutil.copytree(PACKAGE_DIR, package_dir)
        manifest_path = package_dir / "package_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["status"] = "draft"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        path = package_dir / filename
        document = json.loads(path.read_text(encoding="utf-8"))
        mutate(document)
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return package_dir

    def assert_validation_context(
        self,
        package_dir: Path,
        *,
        day: int,
        node: str,
        field: str,
    ) -> ContentValidationError:
        with self.assertRaises(ContentValidationError) as raised:
            FileScriptPackageLoader().load(package_dir)
        error = raised.exception
        self.assertEqual("pkg_gameplay_v3", error.details.get("package"))
        self.assertEqual(day, error.details.get("day"))
        self.assertEqual(node, error.details.get("node"))
        self.assertEqual(field, error.details.get("field"))
        self.assertTrue(error.message.strip())
        return error

    def test_unknown_visible_speaker_is_rejected_with_actionable_context(self) -> None:
        def mutate(document: dict) -> None:
            beat = next(item for item in document["beats"] if item["story_day"] == 17)
            beat["opening_blocks"][1]["speaker"] = "不存在的人"

        package_dir = self.mutate_package("story_beats.json", mutate)

        error = self.assert_validation_context(
            package_dir,
            day=17,
            node="d17_feng_words",
            field="speaker",
        )
        self.assertIn("人物", error.message)

    def test_proactive_target_cannot_open_before_the_character_is_introduced(self) -> None:
        def mutate(document: dict) -> None:
            opportunity = next(
                item
                for item in document["opportunities"]
                if item["opportunity_id"] == "opp_31_luo_jian_contact"
            )
            opportunity["day_min"] = 1

        package_dir = self.mutate_package("interaction_opportunities.json", mutate)

        error = self.assert_validation_context(
            package_dir,
            day=1,
            node="opp_31_luo_jian_contact",
            field="npc_id",
        )
        self.assertIn("介绍", error.message)

    def test_missing_scene_asset_is_rejected_at_the_referencing_block(self) -> None:
        def mutate(document: dict) -> None:
            beat = next(item for item in document["beats"] if item["story_day"] == 17)
            beat["opening_blocks"][0]["scene_id"] = "C99_S99"

        package_dir = self.mutate_package("story_beats.json", mutate)

        error = self.assert_validation_context(
            package_dir,
            day=17,
            node="d17_feng_arrival",
            field="scene_id",
        )
        self.assertIn("场景", error.message)

    def test_missing_decision_prerequisite_is_rejected_with_decision_context(self) -> None:
        def mutate(document: dict) -> None:
            decision = next(
                item for item in document["decisions"] if item["decision_id"] == "dp2_01"
            )
            decision["presentation_blocks"] = []

        package_dir = self.mutate_package("decisions.json", mutate)

        error = self.assert_validation_context(
            package_dir,
            day=17,
            node="dp2_01",
            field="presentation_blocks",
        )
        self.assertIn("铺垫", error.message)

    def test_blank_non_story_day_requires_an_explicit_free_action_prompt(self) -> None:
        def mutate(document: dict) -> None:
            beat = next(item for item in document["beats"] if item["story_day"] == 19)
            beat["day_mode"] = "playable"

        package_dir = self.mutate_package("story_beats.json", mutate)

        error = self.assert_validation_context(
            package_dir,
            day=19,
            node="beat_d19_m2",
            field="day_mode",
        )
        self.assertIn("自由行动", error.message)

    def test_duplicate_scheduled_decision_is_rejected_at_transition_reference(self) -> None:
        def mutate(document: dict) -> None:
            beat = next(item for item in document["beats"] if item["story_day"] == 28)
            beat["decision_ids"].append("dp2_08")

        package_dir = self.mutate_package("story_beats.json", mutate)

        error = self.assert_validation_context(
            package_dir,
            day=28,
            node="beat_d28_m2",
            field="decision_ids",
        )
        self.assertIn("重复", error.message)

    def test_hidden_metric_delta_in_player_consequence_is_rejected_at_load(self) -> None:
        def mutate(document: dict) -> None:
            decision = next(
                item for item in document["decisions"] if item["decision_id"] == "dp2_07"
            )
            decision["options"][0]["consequence"] = "政治资本 +5 到 +10。"

        package_dir = self.mutate_package("decisions.json", mutate)
        with self.assertRaises(ContentValidationError):
            FileScriptPackageLoader().load(package_dir)

    def test_adjacent_player_punctuation_is_rejected_at_load(self) -> None:
        def mutate(document: dict) -> None:
            beat = next(item for item in document["beats"] if item["story_day"] == 13)
            beat["opening_blocks"][0]["text"] = "首轮攻坚已经排定，。"

        package_dir = self.mutate_package("story_beats.json", mutate)
        with self.assertRaises(ContentValidationError):
            FileScriptPackageLoader().load(package_dir)

    def test_d13_to_d30_decisions_use_event_specific_premises_and_followups(self) -> None:
        package = FileScriptPackageLoader().load(PACKAGE_DIR)
        generic_fragments = (
            "现场的事实、人物立场和时间压力已经摆到你面前",
            "决定已经写入当天案卷",
            "请根据已经展开的现场信息作出决定",
        )

        for decision in package.decisions.values():
            if not 13 <= decision.story_day <= 30:
                continue
            visible_text = "\n".join((
                decision.prompt,
                *(item.text for item in decision.presentation_blocks),
                *(item.text for item in decision.followup_blocks),
            ))
            self.assertFalse(
                any(fragment in visible_text for fragment in generic_fragments),
                f"D{decision.story_day}/{decision.decision_id} still uses a generic premise",
            )

    def test_d18_premise_does_not_contradict_the_same_day_opening(self) -> None:
        package = FileScriptPackageLoader().load(PACKAGE_DIR)
        normal_opening = "\n".join(
            item.text
            for item in package.story_day(18).opening_blocks
            if item.is_visible(origin_id="technical", flags=set())
        )
        normal_premise = "\n".join(
            item.text
            for item in package.decisions["dp2_02"].presentation_blocks
            if item.is_visible(origin_id="technical", flags=set())
        )
        broken_flags = {"与钱伟撕破脸"}
        phone_opening = "\n".join(
            item.text
            for item in package.story_day(18).opening_blocks
            if item.is_visible(origin_id="technical", flags=broken_flags)
        )
        phone_premise = "\n".join(
            item.text
            for item in package.decisions["dp2_02"].presentation_blocks
            if item.is_visible(origin_id="technical", flags=broken_flags)
        )

        self.assertIn("钱伟坐在你办公室", normal_opening)
        self.assertIn("茶叶盒里压着", normal_premise)
        self.assertNotIn("钱伟没有登门", normal_premise)
        self.assertIn("钱伟没有登门", phone_opening)
        self.assertIn("赵建国", phone_premise)
        self.assertNotIn("钱伟坐在你办公室", phone_opening)

    def test_every_emitted_story_entry_has_a_stable_content_instance_id(self) -> None:
        package = FileScriptPackageLoader().load(PACKAGE_DIR)
        from serious_game_backend.application.game_session_service import (
            GameSessionService,
        )
        from serious_game_backend.application.event_service import EventService
        from serious_game_backend.infrastructure.repositories.memory import (
            InMemoryGameSessionRepository,
            InMemoryRuntimeTransactionRepository,
            InMemoryScriptPackageRepository,
            InMemorySessionRequestRepository,
        )

        sessions = InMemoryGameSessionRepository()
        requests = InMemorySessionRequestRepository()
        service = GameSessionService(
            sessions,
            requests,
            InMemoryRuntimeTransactionRepository(sessions, None, requests),
            InMemoryScriptPackageRepository([package]),
            StoryFlowService(),
            EventService(),
        )
        session = service.start_session(
            account_id="acct_story_semantics",
            package_id="pkg_gameplay_v3",
            client_request_id="story-semantics-session",
            origin_id="technical",
        )
        flow = StoryFlowService()
        flow.resolve_decision(
            session,
            package,
            decision_id="ev1_01_reception_bag",
            option_id="a_reject_on_site",
        )

        self.assertTrue(session.narrative_feed)
        self.assertTrue(
            all(item.content_instance_id for item in session.narrative_feed),
            "retry/replay dedupe requires every emitted story entry to have an ID",
        )

    def test_schema4_requires_a_complete_d1_d90_acceptance_matrix(self) -> None:
        package = FileScriptPackageLoader().load(PACKAGE_DIR)

        matrix = package.story_acceptance_matrix
        self.assertEqual(list(range(1, 91)), [item["story_day"] for item in matrix])
        required_fields = {
            "story_day",
            "state",
            "node_id",
            "previous_settlement_dependency",
            "opening_block_ids",
            "scene_ids",
            "visible_speakers",
            "introduced_npc_ids",
            "prerequisite_narrative_ids",
            "decision_ids",
            "decision_display_node_ids",
            "outcome_transition_ids",
            "free_action_prompt",
        }
        for row in matrix:
            self.assertTrue(required_fields.issubset(row), f"D{row['story_day']} matrix fields")
            if row["state"] == "free_action":
                self.assertTrue(row["free_action_prompt"].strip())
            else:
                self.assertTrue(row["opening_block_ids"] or row["decision_ids"])

    def test_acceptance_matrix_rejects_a_wrong_decision_display_reference(self) -> None:
        def mutate(document: dict) -> None:
            row = next(item for item in document["days"] if item["story_day"] == 17)
            row["decision_display_node_ids"] = ["missing_display_node"]

        package_dir = self.mutate_package("story_acceptance_matrix.json", mutate)

        error = self.assert_validation_context(
            package_dir,
            day=17,
            node="beat_d17_m2",
            field="decision_display_node_ids",
        )
        self.assertIn("决策", error.message)

    def test_v3_is_the_default_and_v2_is_runtime_retired_without_file_edits(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            defaults = Settings.from_env()
        self.assertEqual("pkg_gameplay_v3", defaults.default_package_id)

        settings = Settings(
            environment="test",
            content_root=BACKEND_ROOT / "content" / "packages",
            default_package_id="pkg_gameplay_v3",
            repository="memory",
            role_llm_provider="fake",
        )
        container = build_container(settings)
        self.assertEqual("retired", container.packages.get("pkg_gameplay_v2").status)
        self.assertNotEqual("retired", container.packages.get("pkg_gameplay_v3").status)

    def test_existing_v2_session_is_review_only_and_rejects_writes(self) -> None:
        settings = Settings(
            environment="test",
            content_root=BACKEND_ROOT / "content" / "packages",
            default_package_id="pkg_gameplay_v2",
            repository="memory",
            role_llm_provider="fake",
        )
        container = build_container(settings)
        client = TestClient(create_app(settings, container=container))
        headers = {"X-Account-ID": "acct_existing_v2"}
        created = client.post(
            "/api/game/session",
            headers=headers,
            json={"client_request_id": "existing-v2", "package_id": "pkg_gameplay_v2"},
        )
        self.assertEqual(201, created.status_code, created.text)
        session_id = created.json()["session_id"]
        legacy = container.packages.get("pkg_gameplay_v2")
        container.packages._items[legacy.package_id] = replace(legacy, status="retired")

        sessions = client.get("/api/game/sessions", headers=headers)
        summary = next(
            item for item in sessions.json()["sessions"] if item["session_id"] == session_id
        )
        self.assertEqual("review_only", summary["mode"])
        self.assertEqual(
            200,
            client.get(f"/api/game/session/{session_id}", headers=headers).status_code,
        )
        stored = container.sessions.get_owned(session_id, "acct_existing_v2")
        write = client.post(
            f"/api/game/session/{session_id}/end-day",
            headers=headers,
            json={
                "client_action_id": "existing-v2-write",
                "state_version": stored.state_version,
            },
        )
        self.assertEqual(409, write.status_code, write.text)
        self.assertEqual("PACKAGE_RETIRED", write.json()["error"]["code"])


if __name__ == "__main__":
    unittest.main()
