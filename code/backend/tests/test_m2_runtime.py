from __future__ import annotations

from pathlib import Path
import hashlib
import unittest

from fastapi.testclient import TestClient

from serious_game_backend.api.app import create_app
from serious_game_backend.bootstrap import build_container
from serious_game_backend.config import Settings


BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parents[1]


class M2RuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        settings = Settings(
            environment="test",
            content_root=BACKEND_ROOT / "content" / "packages",
            repository="memory",
            role_llm_provider="fake",
        )
        self.container = build_container(settings)
        self.client = TestClient(create_app(settings, container=self.container))
        self.headers = {"X-Account-ID": "acct_m2"}
        created = self.client.post(
            "/api/game/session",
            json={
                "client_request_id": "m2-new-game-0001",
                "origin_id": "technical",
            },
            headers=self.headers,
        )
        self.assertEqual(201, created.status_code)
        self.session_id = created.json()["session_id"]

    def action(self, payload: dict) -> dict:
        response = self.client.post(
            f"/api/game/session/{self.session_id}/action",
            json=payload,
            headers=self.headers,
        )
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def end_day(self, version: int, key: str) -> dict:
        response = self.client.post(
            f"/api/game/session/{self.session_id}/end-day",
            json={"client_action_id": key, "state_version": version},
            headers=self.headers,
        )
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def drain_decisions(self, result: dict, prefix: str) -> dict:
        index = 0
        pending = result["visible_state"].get("pending_decision")
        while pending is not None:
            parameters = {}
            if pending.get("input_kind") == "allocation":
                fields = pending["input_schema"]["fields"]
                parameters = {
                    "allocations": {
                        field: (150 if position == 0 else 0)
                        for position, field in enumerate(fields)
                    }
                }
            result = self.action({
                "input_mode": "decision",
                "client_action_id": f"{prefix}-decision-{index:03d}",
                "state_version": result["state_version"],
                "decision_id": pending["decision_id"],
                "option_id": next(
                    item["option_id"] for item in pending["options"]
                    if item.get("available", True)
                ),
                "parameters": parameters,
            })
            index += 1
            pending = result["visible_state"].get("pending_decision")
        return result

    def reach_d3(self) -> dict:
        first = self.action({
            "input_mode": "decision",
            "client_action_id": "m2-d1-decision",
            "state_version": 1,
            "decision_id": "ev1_01_reception_bag",
            "option_id": "a_reject_on_site",
        })
        d2 = self.end_day(first["state_version"], "m2-end-d1")
        taskforce = self.action({
            "input_mode": "decision",
            "client_action_id": "m2-d2-decision",
            "state_version": d2["state_version"],
            "decision_id": "dp1_01_taskforce_faction_map",
            "option_id": "c_public_rules_covert_check",
        })
        started = self.action({
            "input_mode": "conversation_start",
            "client_action_id": "m2-d2-start",
            "state_version": taskforce["state_version"],
            "opportunity_id": "opp_d02_wu_xiuying_first_talk",
            "target_npc_id": "npc_wu_xiuying",
        })
        conversation_id = started["conversation"]["conversation_id"]
        talk = self.action({
            "input_mode": "free_text",
            "client_action_id": "m2-d2-talk",
            "state_version": started["state_version"],
            "conversation_id": conversation_id,
            "opportunity_id": "opp_d02_wu_xiuying_first_talk",
            "target_npc_id": "npc_wu_xiuying",
            "player_text": "我想先听您说真话。",
        })
        closed = self.action({
            "input_mode": "conversation_end",
            "client_action_id": "m2-d2-close",
            "state_version": talk["state_version"],
            "conversation_id": conversation_id,
        })
        return self.end_day(closed["state_version"], "m2-end-d2")

    def test_full_story_clock_reaches_d90_and_builds_review(self) -> None:
        result = self.reach_d3()
        self.assertEqual(3, result["visible_state"]["story"]["day"])
        stops = []
        for index in range(80):
            if result["visible_state"]["status"] == "ended":
                break
            result = self.drain_decisions(result, f"m2-stop-{index:02d}")
            result = self.end_day(
                result["state_version"], f"m2-auto-end-{index:02d}"
            )
            stops.append(result["visible_state"]["story"]["day"])
        self.assertEqual(
            [
                5, 7, 8, 9, 11, 12, 13, 15, 16, 17, 18, 20, 21, 25,
                26, 27, 28, 29, 31, 32, 34, 36, 38, 41, 42, 43, 44,
                45, 46, 48, 49, 51, 53, 55, 56, 57, 58, 59, 60,
                61, 63, 64, 71, 72, 73, 74, 75, 76, 77, 78,
                79, 80, 81, 82, 83, 84, 85, 87, 89, 90,
            ],
            stops,
        )
        self.assertEqual("ended", result["visible_state"]["status"])
        self.assertEqual(0, result["visible_state"]["ledger"]["days_left"])
        self.assertIsNotNone(result["ending"])

        review = self.client.get(
            f"/api/game/session/{self.session_id}/review", headers=self.headers
        )
        self.assertEqual(200, review.status_code)
        document = review.json()
        self.assertEqual(89, len(document["night_timeline"]))
        event_ids = {item["event_id"] for item in document["visible_events"]}
        self.assertTrue({
            "event_d31_municipal_inspection_arrival",
            "event_d45_municipal_inspection_departure",
            "event_d59_environmental_reception_arrival",
            "event_d90_final_acceptance",
        }.issubset(event_ids))
        self.assertEqual(result["ending"], document["ending"])
        self.assertEqual(75, len(document["decision_timeline"]))
        allocation = next(
            item for item in document["decision_timeline"]
            if item["decision_id"] == "dp2_10"
        )
        self.assertEqual(150, sum(allocation["parameters"].values()))

    def test_map_and_complete_package_validation_are_player_safe(self) -> None:
        result = self.reach_d3()
        map_response = self.client.get(
            f"/api/game/session/{self.session_id}/map", headers=self.headers
        )
        self.assertEqual(200, map_response.status_code)
        locations = {
            item["location_id"]: item for item in map_response.json()["locations"]
        }
        self.assertEqual("available", locations["loc_liulin_village"]["visual_state"])
        self.assertNotIn("trust_score", map_response.text)
        for suffix in ("map", "review"):
            forbidden = self.client.get(
                f"/api/game/session/{self.session_id}/{suffix}",
                headers={"X-Account-ID": "acct_other"},
            )
            self.assertEqual(404, forbidden.status_code)

        validation = self.client.get(
            "/api/game/package/validation", headers=self.headers
        )
        self.assertEqual(200, validation.status_code)
        report = validation.json()
        self.assertTrue(report["valid"])
        self.assertEqual(90, report["counts"]["story_days"])
        self.assertEqual(62, report["counts"]["decision_catalog"])
        self.assertEqual(14, report["counts"]["event_catalog"])
        self.assertEqual(24, report["counts"]["main_endings"])
        self.assertEqual(95, report["counts"]["sub_endings"])
        self.assertEqual(29, report["counts"]["npc_role_profiles"])
        self.assertEqual(18, report["counts"]["facts_and_clues"])
        self.assertEqual(32, report["counts"]["interaction_opportunities"])
        self.assertEqual(5, report["counts"]["sorting_decisions"])
        self.assertEqual(1, report["counts"]["allocation_decisions"])
        self.assertEqual(80, report["counts"]["runtime_decisions"])
        self.assertEqual(29, report["counts"]["source_night_blocks"])
        self.assertEqual(9, report["counts"]["conditional_night_rules"])
        source_hash = hashlib.sha256((REPO_ROOT / "最终剧本.md").read_bytes()).hexdigest()
        self.assertEqual(f"sha256:{source_hash}", report["source_sha256"])
        self.assertEqual(3, result["visible_state"]["story"]["day"])


if __name__ == "__main__":
    unittest.main()
