from __future__ import annotations

from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from serious_game_backend.api.app import create_app
from serious_game_backend.bootstrap import build_container
from serious_game_backend.config import Settings


BACKEND_ROOT = Path(__file__).resolve().parents[1]


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        settings = Settings(
            environment="test",
            content_root=BACKEND_ROOT / "content" / "packages",
            repository="memory",
            role_llm_provider="fake",
        )
        self.client = TestClient(create_app(settings, build_container(settings)))
        self.headers = {"X-Account-ID": "acct_api"}

    def _new_session(self) -> dict:
        response = self.client.post(
            "/api/game/session",
            json={
                "client_request_id": "api-new-game-0001",
                "origin_id": "technical",
            },
            headers=self.headers,
        )
        self.assertEqual(201, response.status_code, response.text)
        return response.json()

    def test_health_and_new_game(self) -> None:
        self.assertEqual(200, self.client.get("/health/live").status_code)
        result = self._new_session()
        self.assertEqual(1, result["state_version"])
        self.assertEqual(8, result["ledger"]["action_points"]["remaining"])
        self.assertEqual(
            "ev1_01_reception_bag",
            result["pending_decision"]["decision_id"],
        )
        self.assertEqual(4, len(result["pending_decision"]["options"]))
        self.assertEqual("technical", result["story"]["origin"]["origin_id"])
        self.assertNotIn("env_clue", result)

        origins = self.client.get("/api/game/origins", headers=self.headers)
        self.assertEqual(200, origins.status_code, origins.text)
        self.assertEqual(5, len(origins.json()["origins"]))

    def test_action_contract_and_ownership(self) -> None:
        session = self._new_session()
        session_id = session["session_id"]
        response = self.client.post(
            f"/api/game/session/{session_id}/action",
            json={
                "input_mode": "decision",
                "client_action_id": "api-action-0001",
                "state_version": 1,
                "decision_id": "ev1_01_reception_bag",
                "option_id": "a_reject_on_site",
            },
            headers=self.headers,
        )
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(2, response.json()["state_version"])
        self.assertIsNone(response.json()["visible_state"]["pending_decision"])
        self.assertEqual(
            8,
            response.json()["visible_state"]["ledger"]["action_points"]["remaining"],
        )

        other = self.client.get(
            f"/api/game/session/{session_id}", headers={"X-Account-ID": "acct_other"}
        )
        self.assertEqual(404, other.status_code)

    def test_free_text_stays_closed_until_opportunity_package_is_ready(self) -> None:
        session = self._new_session()
        response = self.client.post(
            f"/api/game/session/{session['session_id']}/action",
            json={
                "input_mode": "conversation_start",
                "client_action_id": "api-free-text-1",
                "state_version": 1,
                "opportunity_id": "opp_missing",
                "target_npc_id": "npc_zhou_dashan",
            },
            headers=self.headers,
        )
        self.assertEqual(409, response.status_code)
        self.assertEqual("DECISION_REQUIRED", response.json()["error"]["code"])

    def test_request_contract_rejects_unknown_fields(self) -> None:
        session = self._new_session()
        response = self.client.post(
            f"/api/game/session/{session['session_id']}/action",
            json={
                "input_mode": "tool",
                "client_action_id": "api-action-extra-1",
                "state_version": 1,
                "action_id": "home_visit",
                "opportunity_id": "opp_missing",
                "unexpected_hidden_delta": 99,
            },
            headers=self.headers,
        )
        self.assertEqual(422, response.status_code)

    def test_conversation_contract_rejects_mixed_mode_fields(self) -> None:
        session = self._new_session()
        response = self.client.post(
            f"/api/game/session/{session['session_id']}/action",
            json={
                "input_mode": "conversation_start",
                "client_action_id": "api-conversation-mixed-1",
                "state_version": 1,
                "opportunity_id": "opp_missing",
                "target_npc_id": "npc_wu_xiuying",
                "player_text": "不应在开始请求中出现",
            },
            headers=self.headers,
        )
        self.assertEqual(422, response.status_code)

    def test_tool_contract_requires_opportunity_id(self) -> None:
        session = self._new_session()
        response = self.client.post(
            f"/api/game/session/{session['session_id']}/action",
            json={
                "input_mode": "tool",
                "client_action_id": "api-tool-no-opportunity-1",
                "state_version": 1,
                "action_id": "home_visit",
            },
            headers=self.headers,
        )
        self.assertEqual(422, response.status_code)

    def test_operation_query_is_scoped_to_owned_session(self) -> None:
        session = self._new_session()
        session_id = session["session_id"]
        self.client.post(
            f"/api/game/session/{session_id}/action",
            json={
                "input_mode": "decision",
                "client_action_id": "api-operation-1",
                "state_version": 1,
                "decision_id": "ev1_01_reception_bag",
                "option_id": "c_return_next_day",
            },
            headers=self.headers,
        )
        own = self.client.get(
            f"/api/game/session/{session_id}/operations/api-operation-1",
            headers=self.headers,
        )
        self.assertEqual(200, own.status_code)
        other = self.client.get(
            f"/api/game/session/{session_id}/operations/api-operation-1",
            headers={"X-Account-ID": "acct_other"},
        )
        self.assertEqual(404, other.status_code)

    def test_m1_incremental_vertical_slice_reaches_d3(self) -> None:
        session = self._new_session()
        session_id = session["session_id"]

        opening = self.client.get(
            f"/api/game/session/{session_id}/view?after=0",
            headers=self.headers,
        )
        self.assertEqual(200, opening.status_code, opening.text)
        opening_body = opening.json()
        self.assertTrue(opening_body["commands"]["can_choose"])
        self.assertFalse(opening_body["commands"]["can_act"])
        self.assertFalse(opening_body["commands"]["can_end_day"])
        self.assertGreater(len(opening_body["feed"]["items"]), 0)
        opening_cursor = opening_body["feed"]["cursor"]

        action_catalog = self.client.get(
            f"/api/game/session/{session_id}/actions",
            headers=self.headers,
        )
        self.assertEqual(200, action_catalog.status_code, action_catalog.text)
        self.assertTrue(all(
            not item["available"] for item in action_catalog.json()["actions"]
        ))
        self.assertTrue(all(
            item["unavailable_reason"] == "必须先处理当前决策"
            for item in action_catalog.json()["actions"]
        ))

        decision = self.client.post(
            f"/api/game/session/{session_id}/action",
            json={
                "input_mode": "decision",
                "client_action_id": "api-d1-decision-1",
                "state_version": 1,
                "decision_id": "ev1_01_reception_bag",
                "option_id": "b_file_with_discipline",
            },
            headers=self.headers,
        )
        self.assertEqual(200, decision.status_code, decision.text)

        consequence = self.client.get(
            f"/api/game/session/{session_id}/feed?after={opening_cursor}",
            headers=self.headers,
        )
        self.assertEqual(200, consequence.status_code, consequence.text)
        self.assertEqual(["consequence"], [
            item["kind"] for item in consequence.json()["items"]
        ])
        consequence_cursor = consequence.json()["cursor"]

        ended = self.client.post(
            f"/api/game/session/{session_id}/end-day",
            json={
                "client_action_id": "api-d1-end-day-1",
                "state_version": 2,
                "active_rest": False,
            },
            headers=self.headers,
        )
        self.assertEqual(200, ended.status_code, ended.text)
        self.assertEqual(2, ended.json()["visible_state"]["story"]["day"])

        day_two = self.client.get(
            f"/api/game/session/{session_id}/view?after={consequence_cursor}",
            headers=self.headers,
        )
        self.assertEqual(200, day_two.status_code, day_two.text)
        day_two_body = day_two.json()
        self.assertEqual(2, day_two_body["state"]["story"]["day"])
        self.assertFalse(day_two_body["commands"]["can_act"])
        self.assertFalse(day_two_body["commands"]["can_end_day"])
        self.assertTrue(day_two_body["commands"]["can_choose"])
        self.assertEqual(
            "dp1_01_taskforce_faction_map",
            day_two_body["state"]["pending_decision"]["decision_id"],
        )
        self.assertIn("night", [item["kind"] for item in day_two_body["feed"]["items"]])
        self.assertIn("morning", [item["kind"] for item in day_two_body["feed"]["items"]])

        taskforce = self.client.post(
            f"/api/game/session/{session_id}/action",
            json={
                "input_mode": "decision",
                "client_action_id": "api-d2-taskforce-1",
                "state_version": 3,
                "decision_id": "dp1_01_taskforce_faction_map",
                "option_id": "c_public_rules_covert_check",
            },
            headers=self.headers,
        )
        self.assertEqual(200, taskforce.status_code, taskforce.text)

        opportunities = self.client.get(
            f"/api/game/session/{session_id}/opportunities",
            headers=self.headers,
        )
        self.assertEqual(200, opportunities.status_code, opportunities.text)
        self.assertEqual(
            ["opp_d02_wu_xiuying_first_talk"],
            [
                item["opportunity_id"]
                for item in opportunities.json()["opportunities"]
            ],
        )
        first_opportunity = opportunities.json()["opportunities"][0]
        self.assertEqual("吴秀英", first_opportunity["npc_name"])
        self.assertEqual("村民代表，退休教师", first_opportunity["npc_title"])
        self.assertIn("退休教师", first_opportunity["npc_introduction"])
        self.assertEqual("入户走访", first_opportunity["action_name"])
        self.assertIn("剧情后续交谈", first_opportunity["conversation_context"])

        started = self.client.post(
            f"/api/game/session/{session_id}/action",
            json={
                "input_mode": "conversation_start",
                "client_action_id": "api-d2-wu-start-1",
                "state_version": 4,
                "opportunity_id": "opp_d02_wu_xiuying_first_talk",
                "target_npc_id": "npc_wu_xiuying",
            },
            headers=self.headers,
        )
        self.assertEqual(200, started.status_code, started.text)
        self.assertEqual("active", started.json()["conversation"]["status"])
        self.assertIn("菜", started.json()["narrative"])
        conversation_id = started.json()["conversation"]["conversation_id"]
        self.assertEqual(
            7,
            started.json()["visible_state"]["ledger"]["action_points"]["remaining"],
        )

        talk = self.client.post(
            f"/api/game/session/{session_id}/action",
            json={
                "input_mode": "free_text",
                "client_action_id": "api-d2-wu-talk-1",
                "state_version": 5,
                "conversation_id": conversation_id,
                "opportunity_id": "opp_d02_wu_xiuying_first_talk",
                "target_npc_id": "npc_wu_xiuying",
                "player_text": "吴老师，我想先听听村里人真正担心什么。",
            },
            headers=self.headers,
        )
        self.assertEqual(200, talk.status_code, talk.text)
        self.assertEqual(6, talk.json()["state_version"])
        self.assertIn("谁的话在谁面前好使", talk.json()["npc_reply"]["text"])
        self.assertEqual("active", talk.json()["conversation"]["status"])
        self.assertEqual(
            7,
            talk.json()["visible_state"]["ledger"]["action_points"]["remaining"],
        )

        second_talk = self.client.post(
            f"/api/game/session/{session_id}/action",
            json={
                "input_mode": "free_text",
                "client_action_id": "api-d2-wu-talk-2",
                "state_version": 6,
                "conversation_id": conversation_id,
                "opportunity_id": "opp_d02_wu_xiuying_first_talk",
                "target_npc_id": "npc_wu_xiuying",
                "player_text": "你必须配合，马上签。",
            },
            headers=self.headers,
        )
        self.assertEqual(200, second_talk.status_code, second_talk.text)
        self.assertEqual("ended", second_talk.json()["conversation"]["status"])
        self.assertEqual("npc", second_talk.json()["conversation"]["ended_by"])
        self.assertIn("提起菜篮", second_talk.json()["conversation"]["exit_narrative"])
        self.assertEqual(
            7,
            second_talk.json()["visible_state"]["ledger"]["action_points"]["remaining"],
        )
        ended_feed = self.client.get(
            f"/api/game/session/{session_id}/feed?after=0", headers=self.headers
        ).json()["items"]
        ended_text = "\n".join(item["text"] for item in ended_feed)
        self.assertIn("提起菜篮转身下坡", ended_text)
        self.assertNotIn("拎着菜篮子径自走了", ended_text)

        knowledge = self.client.get(
            f"/api/game/session/{session_id}/knowledge",
            headers=self.headers,
        )
        self.assertEqual(2, len(knowledge.json()["facts"]))

        ended_d2 = self.client.post(
            f"/api/game/session/{session_id}/end-day",
            json={
                "client_action_id": "api-d2-end-day-1",
                "state_version": 7,
                "active_rest": False,
            },
            headers=self.headers,
        )
        self.assertEqual(200, ended_d2.status_code, ended_d2.text)
        self.assertEqual(3, ended_d2.json()["visible_state"]["story"]["day"])

        next_opportunity = self.client.get(
            f"/api/game/session/{session_id}/opportunities",
            headers=self.headers,
        )
        self.assertEqual([], next_opportunity.json()["opportunities"])
        self.assertEqual(
            "必须先处理当前决策",
            next_opportunity.json()["blocked_reason"],
        )
        state = self.client.get(
            f"/api/game/session/{session_id}", headers=self.headers
        ).json()
        self.assertEqual(
            "dp1_02", state["pending_decision"]["decision_id"]
        )
        latest = self.client.get(
            "/api/game/session/latest-active", headers=self.headers
        )
        self.assertEqual(session_id, latest.json()["session_id"])
        self.assertEqual(3, latest.json()["story"]["day"])


if __name__ == "__main__":
    unittest.main()
