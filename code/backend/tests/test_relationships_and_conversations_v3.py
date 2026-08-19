from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from serious_game_backend.api.app import create_app
from serious_game_backend.application.npc_memory_service import NPCMemoryService
from serious_game_backend.bootstrap import build_container
from serious_game_backend.config import Settings
from serious_game_backend.domain.llm import RoleTurnContext
from serious_game_backend.infrastructure.llm.fake import FakeRoleLLMGateway
from serious_game_backend.infrastructure.repositories.codec import (
    decode_session,
    encode_session,
)
from serious_game_backend.infrastructure.repositories.memory import (
    InMemoryNPCMemoryRepository,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = BACKEND_ROOT / "content" / "packages"


class RelationshipAndConversationV3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            environment="test",
            content_root=PACKAGE_ROOT,
            default_package_id="pkg_gameplay_v3",
            repository="memory",
            role_llm_provider="fake",
        )
        self.runtime = build_container(self.settings)
        self.client = TestClient(create_app(self.settings, self.runtime))
        self.headers = {"X-Account-ID": "acct_relationship_v3"}
        response = self.client.post(
            "/api/game/session",
            headers=self.headers,
            json={
                "client_request_id": "relationship-v3-session-0001",
                "package_id": "pkg_gameplay_v3",
            },
        )
        self.assertEqual(201, response.status_code, response.text)
        self.session_id = response.json()["session_id"]

    def _set_story_state(
        self,
        *,
        day: int,
        flags: set[str] | None = None,
        presented_names: tuple[str, ...] = (),
    ) -> None:
        session = self.runtime.sessions.get_owned(
            self.session_id, "acct_relationship_v3"
        )
        session.pending_decision = None
        session.pending_decision_queue.clear()
        session.flags = set(flags or ())
        session.game_state = replace(session.game_state, story_day=day)
        for name in presented_names:
            session.append_narrative(
                story_day=day,
                kind="narration",
                text=f"{name}已在本段剧情中正式出现。",
            )
        self.runtime.sessions.save(
            session, expected_version=session.state_version
        )

    def _opportunities(self) -> dict:
        response = self.client.get(
            f"/api/game/session/{self.session_id}/opportunities",
            headers=self.headers,
        )
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def _start_and_end_wu_conversation(self, suffix: str) -> str:
        session = self.runtime.sessions.get_owned(
            self.session_id, "acct_relationship_v3"
        )
        started = self.client.post(
            f"/api/game/session/{self.session_id}/action",
            headers=self.headers,
            json={
                "input_mode": "conversation_start",
                "client_action_id": f"conversation-start-{suffix}",
                "state_version": session.state_version,
                "opportunity_id": "opp_d02_wu_xiuying_first_talk",
                "target_npc_id": "npc_wu_xiuying",
            },
        )
        self.assertEqual(200, started.status_code, started.text)
        conversation_id = started.json()["conversation"]["conversation_id"]
        ended = self.client.post(
            f"/api/game/session/{self.session_id}/action",
            headers=self.headers,
            json={
                "input_mode": "free_text",
                "client_action_id": f"conversation-turn-{suffix}",
                "state_version": started.json()["state_version"],
                "conversation_id": conversation_id,
                "opportunity_id": "opp_d02_wu_xiuying_first_talk",
                "target_npc_id": "npc_wu_xiuying",
                "player_text": "你必须配合，马上签。",
            },
        )
        self.assertEqual(200, ended.status_code, ended.text)
        self.assertEqual("ended", ended.json()["conversation"]["status"])
        return conversation_id

    def test_unknown_known_contactable_transitions_hide_every_target_surface(self) -> None:
        self._set_story_state(day=59)
        unknown = self._opportunities()
        self.assertNotIn("npc_gu_keming", json.dumps(unknown, ensure_ascii=False))
        map_body = self.client.get(
            f"/api/game/session/{self.session_id}/map", headers=self.headers
        ).json()
        governance = self.client.get(
            f"/api/game/session/{self.session_id}/governance",
            headers=self.headers,
        ).json()
        self.assertNotIn(
            "npc_gu_keming",
            json.dumps({"map": map_body, "governance": governance}, ensure_ascii=False),
        )

        self._set_story_state(day=58, presented_names=("顾克明",))
        known = self._opportunities()
        person = next(
            item for item in known["people"] if item["npc_id"] == "npc_gu_keming"
        )
        self.assertEqual("known", person["contact_state"])
        self.assertNotIn(
            "npc_gu_keming",
            {item["npc_id"] for item in known["opportunities"]},
        )

        self._set_story_state(day=59, presented_names=("顾克明",))
        contactable = self._opportunities()
        person = next(
            item
            for item in contactable["people"]
            if item["npc_id"] == "npc_gu_keming"
        )
        self.assertEqual("contactable", person["contact_state"])
        self.assertIn(
            "npc_gu_keming",
            {item["npc_id"] for item in contactable["opportunities"]},
        )

    def test_people_dto_uses_qualitative_bands_and_leaks_no_private_state(self) -> None:
        self._set_story_state(
            day=2,
            flags={"flag_clan_map"},
            presented_names=("吴秀英", "王芳"),
        )
        body = self._opportunities()
        self.assertIn("people", body)
        person = next(
            item for item in body["people"] if item["npc_id"] == "npc_wu_xiuying"
        )
        self.assertEqual("contactable", person["contact_state"])
        self.assertIn(person["trust_band"], {"closed", "guarded", "working", "trusted"})
        self.assertIn(
            person["attitude_band"],
            {"hostile", "resistant", "neutral", "cooperative", "supportive"},
        )
        self.assertIn(
            person["anxiety_band"],
            {"calm", "uneasy", "worried", "strained", "critical"},
        )
        self.assertLessEqual(len(person["recent_change_reasons"]), 3)
        ambient = next(
            item for item in body["people"] if item["npc_id"] == "npc_wang_fang"
        )
        self.assertEqual("known", ambient["contact_state"])
        self.assertEqual("not_assessed", ambient["trust_band"])
        serialized = json.dumps(body, ensure_ascii=False)
        for forbidden in (
            "trust_score",
            "attitude_score",
            "anxiety_score",
            "big_five",
            "hidden_demand",
            "hidden_relation",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_fake_role_behavior_changes_with_safe_qualitative_context(self) -> None:
        gateway = FakeRoleLLMGateway()
        base = RoleTurnContext(
            session_id="sess_context",
            npc_id="npc_wu_xiuying",
            player_text="我想听听你的看法。",
            story_day=2,
            opportunity_id="opp_context",
        )
        guarded = gateway.run_turn(replace(
            base,
            visible_world_context={
                "relationship_context": {
                    "trust_band": "guarded",
                    "attitude_band": "resistant",
                    "anxiety_band": "worried",
                }
            },
        ))
        trusted = gateway.run_turn(replace(
            base,
            visible_world_context={
                "relationship_context": {
                    "trust_band": "trusted",
                    "attitude_band": "cooperative",
                    "anxiety_band": "calm",
                }
            },
        ))
        self.assertNotEqual(guarded.dialogue, trusted.dialogue)
        combined = guarded.dialogue + trusted.dialogue
        self.assertNotIn("40", combined)
        self.assertNotIn("trust_score", combined)

    def test_completed_conversations_paginate_filter_and_enforce_ownership(self) -> None:
        self._set_story_state(
            day=2,
            flags={"flag_clan_map"},
            presented_names=("吴秀英",),
        )
        ids = [
            self._start_and_end_wu_conversation("one"),
            self._start_and_end_wu_conversation("two"),
        ]
        first = self.client.get(
            f"/api/game/session/{self.session_id}/conversations?limit=1",
            headers=self.headers,
        )
        self.assertEqual(200, first.status_code, first.text)
        self.assertEqual([ids[0]], [item["conversation_id"] for item in first.json()["items"]])
        self.assertIsNotNone(first.json()["next_cursor"])
        transcript = first.json()["items"][0]["transcript"]
        self.assertEqual(["player", "npc"], [item["speaker"] for item in transcript])

        second = self.client.get(
            f"/api/game/session/{self.session_id}/conversations",
            params={"limit": 1, "cursor": first.json()["next_cursor"]},
            headers=self.headers,
        )
        self.assertEqual(200, second.status_code, second.text)
        self.assertEqual(ids[1], second.json()["items"][0]["conversation_id"])
        filtered = self.client.get(
            f"/api/game/session/{self.session_id}/conversations",
            params={"npc_id": "npc_wu_xiuying", "story_day": 2},
            headers=self.headers,
        )
        self.assertEqual(2, len(filtered.json()["items"]))
        invalid = self.client.get(
            f"/api/game/session/{self.session_id}/conversations?cursor=not-a-cursor",
            headers=self.headers,
        )
        self.assertEqual(422, invalid.status_code, invalid.text)
        invalid_filter = self.client.get(
            f"/api/game/session/{self.session_id}/conversations",
            params={"npc_id": "npc_not_in_this_package"},
            headers=self.headers,
        )
        self.assertEqual(422, invalid_filter.status_code, invalid_filter.text)
        other = self.client.get(
            f"/api/game/session/{self.session_id}/conversations",
            headers={"X-Account-ID": "acct_other"},
        )
        self.assertEqual(404, other.status_code, other.text)

    def test_commitment_memory_is_durable_while_episode_expires(self) -> None:
        repository = InMemoryNPCMemoryRepository()
        service = NPCMemoryService(
            repository,
            compression_threshold=3,
            ttl_days=2,
        )
        episode = service.record(
            session_id="sess_memory",
            account_id="acct_memory",
            npc_id="npc_wu_xiuying",
            operation_id="op_episode",
            story_day=2,
            candidate="吴秀英记得县长今天认真听完了她的意见。",
        )
        commitment = service.record(
            session_id="sess_memory",
            account_id="acct_memory",
            npc_id="npc_wu_xiuying",
            operation_id="op_commitment",
            story_day=2,
            candidate="吴秀英承诺在D60前介绍周大山，当前尚未兑现。",
        )
        disclosure = service.record(
            session_id="sess_memory",
            account_id="acct_memory",
            npc_id="npc_wu_xiuying",
            operation_id="op_disclosure",
            story_day=2,
            candidate="吴秀英披露了周家账本的存放位置。",
        )
        relationship = service.record(
            session_id="sess_memory",
            account_id="acct_memory",
            npc_id="npc_wu_xiuying",
            operation_id="op_relationship",
            story_day=2,
            candidate="吴秀英说明这次关系转折已经发生。",
        )
        self.assertEqual("episode", episode.memory_type)
        self.assertEqual("commitment", commitment.memory_type)
        self.assertEqual(90, commitment.expires_after_day)
        self.assertEqual("npc_wu_xiuying", commitment.actor_id)
        self.assertEqual(60, commitment.due_day)
        self.assertEqual("unresolved", commitment.resolution_state)
        self.assertEqual("disclosure", disclosure.memory_type)
        self.assertEqual(90, disclosure.expires_after_day)
        self.assertEqual("observed", disclosure.resolution_state)
        self.assertEqual("relationship", relationship.memory_type)
        self.assertEqual("observed", relationship.resolution_state)
        context = service.context(
            session_id="sess_memory",
            npc_id="npc_wu_xiuying",
            story_day=2,
            query="周大山",
        )
        self.assertEqual((commitment.content,), context["unresolved_commitments"])
        durable_at_d90 = service.retrieve(
            session_id="sess_memory",
            npc_id="npc_wu_xiuying",
            story_day=90,
            query="介绍周大山",
        )
        self.assertIn(commitment.content, durable_at_d90)
        self.assertIn(disclosure.content, durable_at_d90)
        self.assertIn(relationship.content, durable_at_d90)
        self.assertNotIn(episode.content, durable_at_d90)

        for index in range(2):
            service.record(
                session_id="sess_memory",
                account_id="acct_memory",
                npc_id="npc_wu_xiuying",
                operation_id=f"op_episode_{index}",
                story_day=2,
                candidate=f"第{index + 2}次交谈继续核对补偿规矩。",
            )
        summary = next(
            item
            for item in repository.active_for_npc(
                "sess_memory", "npc_wu_xiuying", 2
            )
            if item.memory_type == "summary"
        )
        self.assertEqual("npc_wu_xiuying", summary.actor_id)
        self.assertIn("内容:", summary.commitment_content)
        self.assertIsNone(summary.due_day)
        self.assertEqual("observed", summary.resolution_state)

    def test_snapshot_codec_defaults_and_round_trips_relationship_state(self) -> None:
        session = self.runtime.sessions.get_owned(
            self.session_id, "acct_relationship_v3"
        )
        legacy = encode_session(session)
        legacy.pop("known_npc_ids", None)
        legacy.pop("contactable_npc_ids", None)
        legacy.pop("relationship_edges", None)
        legacy.pop("completed_conversations", None)
        restored_legacy = decode_session(legacy)
        self.assertEqual(set(), restored_legacy.known_npc_ids)
        self.assertEqual(set(), restored_legacy.contactable_npc_ids)
        self.assertEqual([], restored_legacy.relationship_edges)
        self.assertEqual([], restored_legacy.completed_conversations)

        session.known_npc_ids = {"npc_wu_xiuying"}
        session.contactable_npc_ids = {"npc_wu_xiuying"}
        session.relationship_edges = [{
            "edge_id": "rel_wu_zhou",
            "source_npc_id": "npc_wu_xiuying",
            "target_npc_id": "npc_zhou_dashan",
            "visibility": "suspected",
            "discovery_reason": "吴秀英提到村庄关系",
            "discovery_day": 2,
        }]
        round_trip = decode_session(encode_session(session))
        self.assertEqual(session.known_npc_ids, round_trip.known_npc_ids)
        self.assertEqual(session.contactable_npc_ids, round_trip.contactable_npc_ids)
        self.assertEqual(session.relationship_edges, round_trip.relationship_edges)

    def test_v3_config_registers_all_five_relationship_subnetworks(self) -> None:
        package = self.runtime.packages.get("pkg_gameplay_v3")
        subnetworks = {
            item.get("subnetwork") for item in package.npc_relationships
        }
        self.assertEqual(
            {
                "county_government",
                "corporate_corruption",
                "village_clan",
                "environmental_evidence",
                "external_oversight",
            },
            subnetworks,
        )


class RelationshipSqliteRestartTests(unittest.TestCase):
    def test_new_snapshot_fields_survive_sqlite_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "runtime.sqlite3"
            settings = Settings(
                environment="test",
                content_root=PACKAGE_ROOT,
                default_package_id="pkg_gameplay_v3",
                repository="sqlite",
                database_path=database_path,
                role_llm_provider="fake",
            )
            runtime = build_container(settings)
            client = TestClient(create_app(settings, runtime))
            headers = {"X-Account-ID": "acct_sqlite_relationship"}
            created = client.post(
                "/api/game/session",
                headers=headers,
                json={
                    "client_request_id": "sqlite-relationship-session-0001",
                    "package_id": "pkg_gameplay_v3",
                },
            )
            self.assertEqual(201, created.status_code, created.text)
            session_id = created.json()["session_id"]
            session = runtime.sessions.get_owned(
                session_id, "acct_sqlite_relationship"
            )
            session.known_npc_ids = {"npc_wu_xiuying"}
            session.contactable_npc_ids = {"npc_wu_xiuying"}
            session.relationship_edges = [{
                "edge_id": "rel_restart",
                "source_npc_id": "npc_wu_xiuying",
                "target_npc_id": "npc_zhou_dashan",
                "visibility": "confirmed",
                "discovery_reason": "会谈确认",
                "discovery_day": 2,
            }]
            runtime.sessions.save(session, expected_version=session.state_version)

            restarted = build_container(settings)
            restored = restarted.sessions.get_owned(
                session_id, "acct_sqlite_relationship"
            )
            self.assertEqual({"npc_wu_xiuying"}, restored.known_npc_ids)
            self.assertEqual({"npc_wu_xiuying"}, restored.contactable_npc_ids)
            self.assertEqual("confirmed", restored.relationship_edges[0]["visibility"])


if __name__ == "__main__":
    unittest.main()
