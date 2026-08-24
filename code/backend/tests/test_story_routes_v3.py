from __future__ import annotations

from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from serious_game_backend.api.app import create_app
from serious_game_backend.bootstrap import build_container
from serious_game_backend.config import Settings


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = BACKEND_ROOT / "content" / "packages"


class StoryRoutesV3Tests(unittest.TestCase):
    def build_runner(self, route_index: int) -> tuple[object, TestClient, str, dict[str, str]]:
        settings = Settings(
            environment="test",
            content_root=PACKAGE_ROOT,
            default_package_id="pkg_gameplay_v3",
            repository="memory",
            role_llm_provider="fake",
        )
        container = build_container(settings)
        client = TestClient(create_app(settings, container=container))
        headers = {"X-Account-ID": f"acct_story_route_{route_index}"}
        response = client.post(
            "/api/game/session",
            headers=headers,
            json={
                "client_request_id": f"story-route-{route_index}",
                "origin_id": ("technical", "grassroots", "integrity")[route_index],
                "package_id": "pkg_gameplay_v3",
            },
        )
        self.assertEqual(201, response.status_code, response.text)
        return container, client, response.json()["session_id"], headers

    def action(self, client, session_id, headers, payload: dict) -> dict:
        response = client.post(
            f"/api/game/session/{session_id}/action",
            headers=headers,
            json=payload,
        )
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def end_day(self, client, session_id, headers, result: dict, key: str) -> dict:
        response = client.post(
            f"/api/game/session/{session_id}/end-day",
            headers=headers,
            json={"client_action_id": key, "state_version": result["state_version"]},
        )
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def drain_required_group_conversation(
        self, client, session_id, headers, result: dict, key: str
    ) -> dict:
        round_index = 0
        while result["visible_state"].get("active_group_conversation"):
            round_index += 1
            response = client.post(
                f"/api/game/session/{session_id}/group-conversation/turn",
                headers=headers,
                json={
                    "state_version": result["state_version"],
                    "player_text": "请各位只围绕已确认的议题逐项说明。",
                    "client_action_id": f"{key}-group-{round_index:02d}",
                },
            )
            self.assertEqual(200, response.status_code, response.text)
            result = response.json()
        return result

    def choose_option(self, pending: dict, route_index: int, decision_index: int) -> dict:
        available = [item for item in pending["options"] if item.get("available", True)]
        if route_index == 0:
            selected = available[0]
        elif route_index == 1:
            selected = available[-1]
        else:
            selected = available[decision_index % len(available)]
        parameters = {}
        if pending.get("input_kind") == "allocation":
            fields = pending["input_schema"]["fields"]
            total = int(pending["input_schema"]["total"])
            target = (0, len(fields) - 1, len(fields) // 2)[route_index]
            parameters = {
                "allocations": {
                    field: total if position == target else 0
                    for position, field in enumerate(fields)
                }
            }
        return {"option_id": selected["option_id"], "parameters": parameters}

    def drain_decisions(
        self,
        container,
        client,
        session_id,
        headers,
        result: dict,
        route_index: int,
        decision_index: int,
    ) -> tuple[dict, int]:
        pending = result["visible_state"].get("pending_decision")
        while pending is not None:
            stored = container.sessions.get_owned(session_id, headers["X-Account-ID"])
            self.assertTrue(
                any(
                    item.decision_id == pending["decision_id"]
                    and item.presentation_phase == "decision"
                    for item in stored.narrative_feed
                ),
                f"{pending['decision_id']} became actionable before its display node",
            )
            choice = self.choose_option(pending, route_index, decision_index)
            result = self.action(
                client,
                session_id,
                headers,
                {
                    "input_mode": "decision",
                    "client_action_id": f"route-{route_index}-decision-{decision_index:03d}",
                    "state_version": result["state_version"],
                    "decision_id": pending["decision_id"],
                    **choice,
                },
            )
            decision_index += 1
            pending = result["visible_state"].get("pending_decision")
        return result, decision_index

    def reach_day_three(self, container, client, session_id, headers, route_index: int) -> tuple[dict, int]:
        session = container.sessions.get_owned(session_id, headers["X-Account-ID"])
        result = {"state_version": session.state_version, "visible_state": {"pending_decision": {
            "decision_id": session.pending_decision.decision_id,
            "input_kind": session.pending_decision.input_kind,
            "input_schema": session.pending_decision.input_schema,
            "options": [
                {"option_id": item.option_id, "available": item.available}
                for item in session.pending_decision.options
            ],
        }}}
        result, decision_index = self.drain_decisions(
            container, client, session_id, headers, result, route_index, 0
        )
        result = self.end_day(client, session_id, headers, result, f"route-{route_index}-end-d1")
        result, decision_index = self.drain_decisions(
            container, client, session_id, headers, result, route_index, decision_index
        )
        started = self.action(client, session_id, headers, {
            "input_mode": "conversation_start",
            "client_action_id": f"route-{route_index}-wu-start",
            "state_version": result["state_version"],
            "opportunity_id": "opp_d02_wu_xiuying_first_talk",
            "target_npc_id": "npc_wu_xiuying",
        })
        conversation_id = started["conversation"]["conversation_id"]
        talked = self.action(client, session_id, headers, {
            "input_mode": "free_text",
            "client_action_id": f"route-{route_index}-wu-talk",
            "state_version": started["state_version"],
            "conversation_id": conversation_id,
            "opportunity_id": "opp_d02_wu_xiuying_first_talk",
            "target_npc_id": "npc_wu_xiuying",
            "player_text": "请把柳林村各户真正担心的事告诉我。",
        })
        closed = self.action(client, session_id, headers, {
            "input_mode": "conversation_end",
            "client_action_id": f"route-{route_index}-wu-end",
            "state_version": talked["state_version"],
            "conversation_id": conversation_id,
        })
        return (
            self.end_day(client, session_id, headers, closed, f"route-{route_index}-end-d2"),
            decision_index,
        )

    def test_three_distinct_fake_routes_reach_d90_without_semantic_leaks(self) -> None:
        route_sequences = []
        for route_index in range(3):
            container, client, session_id, headers = self.build_runner(route_index)
            result, decision_index = self.reach_day_three(
                container, client, session_id, headers, route_index
            )
            visited_days = [3]
            for story_day in range(3, 91):
                if result["visible_state"]["status"] == "ended":
                    break
                result = self.drain_required_group_conversation(
                    client,
                    session_id,
                    headers,
                    result,
                    f"route-{route_index}-day-{story_day:02d}",
                )
                result, decision_index = self.drain_decisions(
                    container,
                    client,
                    session_id,
                    headers,
                    result,
                    route_index,
                    decision_index,
                )
                result = self.end_day(
                    client,
                    session_id,
                    headers,
                    result,
                    f"route-{route_index}-end-{story_day:02d}",
                )
                visited_days.append(result["visible_state"]["story"]["day"])

            self.assertEqual("ended", result["visible_state"]["status"])
            self.assertEqual(90, result["visible_state"]["story"]["day"])
            self.assertEqual(list(range(3, 91)), visited_days)
            stored = container.sessions.get_owned(session_id, headers["X-Account-ID"])
            content_ids = [
                item.content_instance_id
                for item in stored.narrative_feed
                if item.content_instance_id
            ]
            self.assertEqual(len(content_ids), len(set(content_ids)))
            sequence = tuple(
                (item["decision_id"], item["option_id"])
                for item in stored.logs
                if item.get("type") == "decision"
            )
            self.assertGreaterEqual(len(sequence), 70)
            choice_by_decision = dict(sequence)
            if route_index == 0:
                self.assertEqual("a", choice_by_decision["dp2_01"])
                d30_morning = [
                    item.text
                    for item in stored.narrative_feed
                    if item.story_day == 30 and item.kind == "morning_card"
                ]
                self.assertEqual(
                    [
                        "县城茶楼昨晚有人订了包间，订到子夜。",
                        "柳林村昨夜有人挨家串门，说的还是苗喜旺那笔钱。",
                    ],
                    d30_morning,
                )
            if route_index == 1:
                self.assertEqual("d", choice_by_decision["dp2_01"])
                d18_text = "\n".join(
                    item.text for item in stored.narrative_feed if item.story_day == 18
                )
                self.assertIn("赵建国", d18_text)
                self.assertIn("钱伟没有登门", d18_text)
                self.assertNotIn("钱伟坐在你办公室", d18_text)
                self.assertNotIn(
                    "县城茶楼昨晚有人订了包间",
                    "\n".join(
                        item.text
                        for item in stored.narrative_feed
                        if item.story_day in {29, 30}
                    ),
                )
                self.assertEqual(
                    ["县城昨夜无事。"],
                    [
                        item.text
                        for item in stored.narrative_feed
                        if item.story_day == 30 and item.kind == "morning_card"
                    ],
                )
            route_sequences.append(sequence)
            player_text = "\n".join(item.text for item in stored.narrative_feed)
            for marker in (
                "开启旗标", "关闭旗标", "本节点", "状态量", "结局轴",
                "代码照此算", "行动点重置", "轴 T", "flag_",
            ):
                self.assertNotIn(marker, player_text)

        self.assertEqual(3, len(set(route_sequences)))


if __name__ == "__main__":
    unittest.main()
