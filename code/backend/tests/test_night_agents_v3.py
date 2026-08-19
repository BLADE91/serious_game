from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from serious_game_backend.api.app import create_app
from serious_game_backend.application.night_simulation_service import (
    NightSimulationService,
)
from serious_game_backend.application.scripted_delta_resolver import (
    ScriptedDeltaResolver,
)
from serious_game_backend.application.scripted_effect_service import (
    ScriptedEffectService,
)
from serious_game_backend.bootstrap import build_container
from serious_game_backend.config import Settings
from serious_game_backend.domain.llm import NightAgentResult
from serious_game_backend.infrastructure.llm.fake import FakeRoleLLMGateway
from serious_game_backend.infrastructure.script_packages.file_loader import (
    FileScriptPackageLoader,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = BACKEND_ROOT / "content" / "packages" / "pkg_gameplay_v3"


class NightAgentV3ConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.package = FileScriptPackageLoader().load(PACKAGE_DIR)

    def test_registers_five_bounded_relationship_subnetworks(self) -> None:
        expected = {
            "county_government",
            "corporate_corruption",
            "village_clan",
            "environmental_evidence",
            "external_oversight",
        }

        self.assertEqual(expected, set(self.package.relationship_subnetworks))
        for subnetwork_id, subnetwork in self.package.relationship_subnetworks.items():
            self.assertEqual(subnetwork_id, subnetwork["subnetwork_id"])
            self.assertTrue(subnetwork["edge_ids"])
            self.assertTrue(subnetwork["allowed_propagation_topics"])
            self.assertTrue(subnetwork["relationship_visibility_requirements"])
            self.assertTrue(subnetwork["night_action_ids"])
            for edge_id in subnetwork["edge_ids"]:
                edge = next(
                    item
                    for item in self.package.npc_relationships
                    if item["edge_id"] == edge_id
                )
                self.assertEqual(subnetwork_id, edge["subnetwork"])
                self.assertNotEqual(edge["source_npc_id"], edge["target_npc_id"])
                self.assertTrue(edge["allowed_propagation_topics"])
                self.assertTrue(edge["visibility_requirements"])
                self.assertTrue(edge["night_action_ids"])

    def test_has_an_eligible_dynamic_scene_in_each_story_stage(self) -> None:
        stages = ((1, 15), (16, 30), (31, 45), (46, 60), (61, 75), (76, 89))
        scenes = self.package.night_agent_scenes

        for start, end in stages:
            matching = [
                scene
                for scene in scenes
                if start <= int(scene["story_day"]) <= end
            ]
            self.assertTrue(matching, f"missing dynamic night scene for D{start}-D{end}")
            self.assertTrue(all(scene["selection_mode"] == "autonomous" for scene in matching))
        participating = {
            subnetwork_id
            for scene in scenes
            for subnetwork_id in scene["subnetwork_ids"]
        }
        self.assertEqual(set(self.package.relationship_subnetworks), participating)


class RecordingNightGateway(FakeRoleLLMGateway):
    def __init__(self, *, night_fixture: str = "legal") -> None:
        super().__init__(night_fixture=night_fixture)
        self.contexts = []

    def run_night_turn(self, context):
        self.contexts.append(context)
        return super().run_night_turn(context)


class NightAgentV3SettlementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            environment="test",
            content_root=PACKAGE_DIR.parent,
            default_package_id="pkg_gameplay_v3",
            repository="memory",
            role_llm_provider="fake",
        )
        self.runtime = build_container(self.settings)
        self.client = TestClient(create_app(self.settings, self.runtime))
        self.headers = {"X-Account-ID": "acct_night_v3"}
        response = self.client.post(
            "/api/game/session",
            headers=self.headers,
            json={
                "client_request_id": "night-v3-session",
                "package_id": "pkg_gameplay_v3",
            },
        )
        self.assertEqual(201, response.status_code, response.text)
        self.session_id = response.json()["session_id"]
        self.package = self.runtime.packages.get("pkg_gameplay_v3")

    @staticmethod
    def _service(gateway) -> NightSimulationService:
        return NightSimulationService(
            ScriptedEffectService(ScriptedDeltaResolver()),
            night_llm=gateway,
        )

    def _session_on(self, day: int):
        session = self.runtime.sessions.get_owned(self.session_id, "acct_night_v3")
        session.pending_decision = None
        session.pending_decision_queue.clear()
        session.game_state = replace(
            session.game_state,
            story_day=day,
            days_left=91 - day,
        )
        return session

    def test_candidate_contacts_and_actions_are_derived_from_directed_one_hop_edges(self) -> None:
        gateway = RecordingNightGateway()
        session = self._session_on(10)

        self._service(gateway).run_night(session, self.package)

        contexts = {
            item.npc_id: item
            for item in gateway.contexts
            if item.phase == "contact_selection"
        }
        self.assertEqual(("npc_zhao_jianguo",), contexts["npc_sun_qiang"].counterpart_ids)
        self.assertEqual((), contexts["npc_zhao_jianguo"].counterpart_ids)
        self.assertEqual(("county_reporting",), contexts["npc_sun_qiang"].allowed_topics)
        self.assertEqual(
            ("night_hold_position",),
            tuple(item["action_id"] for item in contexts["npc_sun_qiang"].allowed_actions),
        )
        self.assertTrue(all(
            "effects" not in item and "hard_outcome_ids" not in item
            for context in contexts.values()
            for item in context.allowed_actions
        ))

    def test_legal_consensus_settles_registered_outcome_and_audits_idempotently(self) -> None:
        gateway = RecordingNightGateway()
        session = self._session_on(29)
        before = session.game_state.corruption_evidence
        service = self._service(gateway)

        first = service.run_night(session, self.package)
        second = service.run_night(session, self.package)

        exchange = first["agent_exchanges"][0]
        self.assertIs(first, second)
        self.assertEqual(1, len(session.night_logs))
        self.assertEqual(["night_unify_story"], exchange["executed_action_ids"])
        self.assertEqual(
            ["outcome_unify_story"], exchange["resolved_hard_outcome_ids"]
        )
        self.assertGreater(session.game_state.corruption_evidence, before)
        audits = exchange["private_audit"]
        self.assertEqual(2, len(audits))
        self.assertTrue(all(item["validation_verdict"] == "accepted" for item in audits))
        self.assertTrue(all(item["original_proposal"] for item in audits))
        self.assertTrue(all(item["model_audit_reference"] for item in audits))
        self.assertTrue(all(item["resolved_hard_outcome_ids"] == ["outcome_unify_story"] for item in audits))

    def test_illegal_action_contact_topic_and_target_fall_back_without_partial_state(self) -> None:
        for fixture in (
            "illegal_actor",
            "illegal_action",
            "illegal_contact",
            "illegal_topic",
            "illegal_target",
        ):
            with self.subTest(fixture=fixture):
                session = self._session_on(29)
                session.night_logs.clear()
                session.flags.discard("攻守同盟已成")
                session.game_state = replace(session.game_state, corruption_evidence=0)

                record = self._service(
                    RecordingNightGateway(night_fixture=fixture)
                ).run_night(session, self.package)

                self.assertEqual(0, session.game_state.corruption_evidence)
                self.assertNotIn("攻守同盟已成", session.flags)
                audits = record["private_audit"]
                self.assertTrue(any(item["validation_verdict"] == "rejected" for item in audits))
                self.assertTrue(all(
                    item["chosen_fallback"] == "night_hold_position"
                    for item in audits
                    if item["validation_verdict"] == "rejected"
                ))
                self.assertTrue(all(
                    item["resolved_hard_outcome_ids"] == ["outcome_hold_position"]
                    for item in audits
                    if item["validation_verdict"] == "rejected"
                ))
                self.assertEqual([], [
                    item
                    for exchange in record["agent_exchanges"]
                    for item in exchange["executed_action_ids"]
                    if item != "night_hold_position"
                ])

    def test_attempted_hidden_fact_leakage_falls_back_to_hold_position(self) -> None:
        session = self._session_on(29)
        session.game_state = replace(session.game_state, corruption_evidence=0)

        record = self._service(
            RecordingNightGateway(night_fixture="hidden_fact")
        ).run_night(session, self.package)

        self.assertEqual(0, session.game_state.corruption_evidence)
        rejected = [
            item
            for item in record["private_audit"]
            if item["rejection_reason"] == "hidden_fact_leakage"
        ]
        self.assertTrue(rejected)
        self.assertTrue(all(
            item["chosen_fallback"] == "night_hold_position" for item in rejected
        ))

    def test_scene_execution_limit_rejects_later_legal_proposal_before_settlement(self) -> None:
        class SplitActionGateway(FakeRoleLLMGateway):
            def run_night_turn(self, context):
                if context.phase != "action":
                    return super().run_night_turn(context)
                if context.npc_id == "npc_zhao_jianguo":
                    return NightAgentResult(
                        npc_id=context.npc_id,
                        model_id="fake-split-actions",
                        action_id="night_move_originals",
                        topic_ids=("evidence_custody",),
                        rationale="只处理本人经手的材料。",
                    )
                return NightAgentResult(
                    npc_id=context.npc_id,
                    model_id="fake-split-actions",
                    action_id="night_cut_off_counterpart",
                    target_ids=("npc_zhao_jianguo",),
                    topic_ids=("investigation_risk",),
                    rationale="停止互相掩护。",
                )

        session = self._session_on(29)
        session.flags.update({"与钱伟撕破脸", "秘密摸底"})

        record = self._service(SplitActionGateway()).run_night(
            session, self.package
        )

        exchange = record["agent_exchanges"][0]
        self.assertEqual(1, len(exchange["executed_action_ids"]))
        qian = next(
            item
            for item in exchange["action_proposals"]
            if item["npc_id"] == "npc_qian_wei"
        )
        self.assertEqual("night_hold_position", qian["action_id"])
        self.assertTrue(qian["fallback"])
        self.assertEqual("scene_execution_limit", qian["reason"])
        qian_audit = next(
            item
            for item in exchange["private_audit"]
            if item["phase"] == "action" and item["npc_id"] == "npc_qian_wei"
        )
        self.assertEqual("rejected", qian_audit["validation_verdict"])
        self.assertEqual(["outcome_hold_position"], qian_audit["resolved_hard_outcome_ids"])

    def test_unmet_consensus_falls_back_without_applying_consensus_outcome(self) -> None:
        class SplitConsensusGateway(FakeRoleLLMGateway):
            def run_night_turn(self, context):
                if context.phase != "action":
                    return super().run_night_turn(context)
                action_id = (
                    "night_unify_story"
                    if context.npc_id == "npc_qian_wei"
                    else "night_hold_position"
                )
                target_ids = (
                    ("npc_zhao_jianguo",)
                    if action_id == "night_unify_story" else ()
                )
                return NightAgentResult(
                    npc_id=context.npc_id,
                    model_id="fake-split-consensus",
                    action_id=action_id,
                    target_ids=target_ids,
                    topic_ids=("investigation_risk",),
                    rationale="双方尚未形成共同决定。",
                )

        session = self._session_on(29)
        session.game_state = replace(session.game_state, corruption_evidence=0)

        record = self._service(SplitConsensusGateway()).run_night(
            session, self.package
        )

        exchange = record["agent_exchanges"][0]
        self.assertEqual(["night_hold_position"], exchange["executed_action_ids"])
        self.assertEqual(0, session.game_state.corruption_evidence)
        qian = next(
            item for item in exchange["action_proposals"]
            if item["npc_id"] == "npc_qian_wei"
        )
        self.assertEqual("night_hold_position", qian["action_id"])
        self.assertEqual("consensus_not_reached", qian["reason"])
        qian_audit = next(
            item
            for item in exchange["private_audit"]
            if item["phase"] == "action" and item["npc_id"] == "npc_qian_wei"
        )
        self.assertEqual("rejected", qian_audit["validation_verdict"])
        self.assertEqual("consensus_not_reached", qian_audit["rejection_reason"])

    def test_malformed_and_timeout_fall_back_and_public_endpoint_hides_private_audit(self) -> None:
        for fixture in ("malformed", "timeout"):
            with self.subTest(fixture=fixture):
                session = self._session_on(29)
                session.night_logs.clear()
                session.game_state = replace(
                    session.game_state, corruption_evidence=0
                )
                session.flags.discard("攻守同盟已成")
                gateway = RecordingNightGateway(night_fixture=fixture)
                service = self._service(gateway)
                record = service.run_night(session, self.package)
                replay = service.run_night(session, self.package)

                rejected = [
                    item
                    for item in record["private_audit"]
                    if item["validation_verdict"] == "rejected"
                ]
                self.assertTrue(rejected)
                self.assertTrue(all(
                    item["chosen_fallback"] == "night_hold_position"
                    for item in rejected
                ))
                self.assertTrue(record["morning_card"])
                self.assertIs(record, replay)
                self.assertEqual(1, len(session.night_logs))
                self.assertEqual(0, session.game_state.corruption_evidence)
                self.assertNotIn("攻守同盟已成", session.flags)
                self.assertTrue(record["agent_exchanges"])
                self.assertEqual(
                    ["night_hold_position"],
                    record["agent_exchanges"][0]["executed_action_ids"],
                )
                self.assertEqual(
                    ["outcome_hold_position"],
                    record["agent_exchanges"][0]["resolved_hard_outcome_ids"],
                )

        self.runtime.sessions.save(session, expected_version=session.state_version)
        response = self.client.get(
            f"/api/game/session/{self.session_id}/night-dialogues",
            headers=self.headers,
        )
        self.assertEqual(200, response.status_code, response.text)
        payload = response.json()
        self.assertEqual({"story_day", "morning_brief"}, set(payload["nights"][0]))
        for private_key in (
            "private_audit",
            "original_proposal",
            "validation_verdict",
            "rationale",
            "relationship_edges",
            "exact_npc_score",
        ):
            self.assertNotIn(private_key, response.text)

    def test_review_night_timeline_uses_player_safe_field_whitelist(self) -> None:
        session = self._session_on(29)
        self._service(
            RecordingNightGateway(night_fixture="illegal_action")
        ).run_night(session, self.package)
        self.runtime.sessions.save(session, expected_version=session.state_version)

        response = self.client.get(
            f"/api/game/session/{self.session_id}/review",
            headers=self.headers,
        )

        self.assertEqual(200, response.status_code, response.text)
        night = response.json()["night_timeline"][0]
        self.assertEqual(
            {
                "story_day",
                "beat_id",
                "lines",
                "summary",
                "morning_card",
                "propagation_count",
            },
            set(night),
        )
        for private_key in (
            "private_audit",
            "agent_failures",
            "original_proposal",
            "rationale",
            "rejection_reason",
            "model_audit_reference",
        ):
            self.assertNotIn(private_key, response.text)

    def test_illegal_contact_creates_replayable_hold_settlement(self) -> None:
        session = self._session_on(29)
        session.game_state = replace(session.game_state, corruption_evidence=0)
        service = self._service(
            RecordingNightGateway(night_fixture="illegal_contact")
        )

        record = service.run_night(session, self.package)
        replay = service.run_night(session, self.package)

        self.assertIs(record, replay)
        self.assertEqual(1, len(session.night_logs))
        self.assertEqual(0, session.game_state.corruption_evidence)
        self.assertNotIn("攻守同盟已成", session.flags)
        self.assertTrue(record["agent_exchanges"])
        fallback = record["agent_exchanges"][0]
        self.assertEqual(["night_hold_position"], fallback["executed_action_ids"])
        self.assertEqual(
            ["outcome_hold_position"], fallback["resolved_hard_outcome_ids"]
        )
        self.assertIn("有限接触", fallback["public_summary"])

    def test_dialogue_hidden_fact_leakage_blocks_scene_hard_settlement(self) -> None:
        session = self._session_on(29)
        session.game_state = replace(session.game_state, corruption_evidence=0)
        gateway = RecordingNightGateway(night_fixture="hidden_fact_dialogue")

        record = self._service(gateway).run_night(session, self.package)

        self.assertEqual(0, session.game_state.corruption_evidence)
        self.assertNotIn("攻守同盟已成", session.flags)
        self.assertFalse(any(
            context.phase == "action" for context in gateway.contexts
        ))
        exchange = record["agent_exchanges"][0]
        self.assertEqual(["night_hold_position"], exchange["executed_action_ids"])
        self.assertEqual(
            ["outcome_hold_position"], exchange["resolved_hard_outcome_ids"]
        )
        rejected = [
            item
            for item in record["private_audit"]
            if item["rejection_reason"] == "hidden_fact_leakage"
        ]
        self.assertTrue(rejected)
        self.assertNotIn("未公开底稿", "\n".join(record["morning_card"]))

    def test_legal_fixture_is_repeatable_for_same_seed(self) -> None:
        first_session = self._session_on(29)
        first_session.random_seed = "night-v3-repeatable-seed"
        first = self._service(FakeRoleLLMGateway()).run_night(
            first_session, self.package
        )

        created = self.client.post(
            "/api/game/session",
            headers=self.headers,
            json={
                "client_request_id": "night-v3-repeat-session",
                "package_id": "pkg_gameplay_v3",
            },
        )
        self.assertEqual(201, created.status_code, created.text)
        second_session = self.runtime.sessions.get_owned(
            created.json()["session_id"], "acct_night_v3"
        )
        second_session.pending_decision = None
        second_session.pending_decision_queue.clear()
        second_session.random_seed = "night-v3-repeatable-seed"
        second_session.game_state = replace(
            second_session.game_state, story_day=29, days_left=62
        )
        second = self._service(FakeRoleLLMGateway()).run_night(
            second_session, self.package
        )

        first_exchange = first["agent_exchanges"][0]
        second_exchange = second["agent_exchanges"][0]
        for key in (
            "participant_ids",
            "action_proposals",
            "executed_action_ids",
            "resolved_hard_outcome_ids",
            "public_summary",
        ):
            self.assertEqual(first_exchange[key], second_exchange[key])
        self.assertEqual(first_session.flags, second_session.flags)
        self.assertEqual(first_session.game_state, second_session.game_state)


class NightAgentV3FullPlaybackTests(unittest.TestCase):
    def test_fake_playback_reaches_d90_with_89_unique_night_settlements(self) -> None:
        from tests.test_m2_runtime import M2RuntimeTests

        runner = M2RuntimeTests()
        settings = Settings(
            environment="test",
            content_root=PACKAGE_DIR.parent,
            default_package_id="pkg_gameplay_v3",
            repository="memory",
            role_llm_provider="fake",
        )
        runner.container = build_container(settings)
        runner.client = TestClient(create_app(settings, runner.container))
        runner.headers = {"X-Account-ID": "acct_night_v3_playback"}
        created = runner.client.post(
            "/api/game/session",
            headers=runner.headers,
            json={
                "client_request_id": "night-v3-full-playback",
                "origin_id": "technical",
                "package_id": "pkg_gameplay_v3",
            },
        )
        runner.assertEqual(201, created.status_code, created.text)
        runner.session_id = created.json()["session_id"]
        session = runner.container.sessions.get_owned(
            runner.session_id, "acct_night_v3_playback"
        )
        session.random_seed = "night-v3-d1-d90-seed"
        runner.container.sessions.save(
            session, expected_version=session.state_version
        )

        result = runner.reach_d3()
        for index in range(100):
            if result["visible_state"]["status"] == "ended":
                break
            result = runner.drain_decisions(result, f"night-v3-stop-{index:02d}")
            result = runner.end_day(
                result["state_version"], f"night-v3-end-{index:02d}"
            )

        self.assertEqual("ended", result["visible_state"]["status"])
        self.assertEqual(90, result["visible_state"]["story"]["day"])
        stored = runner.container.sessions.get_owned(
            runner.session_id, "acct_night_v3_playback"
        )
        settled_days = [item["story_day"] for item in stored.night_logs]
        self.assertEqual(list(range(1, 90)), settled_days)
        self.assertEqual(89, len(set(settled_days)))
        self.assertTrue(any(
            item.get("private_audit") for item in stored.night_logs
        ))


if __name__ == "__main__":
    unittest.main()
