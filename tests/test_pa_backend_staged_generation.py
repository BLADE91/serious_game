import json
import unittest

from src.generation.script_generator import QwenScriptGenerator
from src.config import PABackendConfig
from src.generation.pa_backend_script_client import PABackendScriptClient


class FakeStageClient:
    def __init__(self) -> None:
        self.calls = []

    def complete(self, messages, temperature=0.2):
        self.calls.append(messages)
        stage = len(self.calls)
        if stage == 1:
            return json.dumps({
                "title": "父母官测试剧本",
                "premise": "基层政策执行遇到多方冲突。",
                "player_role": "乡镇干部",
                "core_conflict": "政策目标、群众利益与执行压力冲突。",
                "initial_game_state": {
                    "day": 1,
                    "action_points": 3,
                    "budget_remaining": 8000,
                    "signed_households": 0,
                    "total_households": 36,
                    "social_stability_index": 70,
                    "political_credit": 70,
                    "cadre_execution_index": 60,
                },
                "citations": [],
            }, ensure_ascii=False)
        if stage == 2:
            return json.dumps({
                "npc_seed": [
                    {
                        "npc_id": f"npc_{index}",
                        "name": f"角色{index}",
                        "npc_type": "villager" if index > 3 else "cadre",
                        "group": "测试组",
                        "trust_to_player": 50,
                        "attitude_score": 50,
                        "anxiety_level": 50,
                        "reference_point": 0,
                        "granovetter_threshold": 50,
                        "core_demand_satisfied": False,
                        "signed": False,
                        "known_info": ["测试信息"],
                        "player_promises": [],
                    }
                    for index in range(1, 13)
                ]
            }, ensure_ascii=False)
        if stage in {3, 4}:
            offset = 0 if stage == 3 else 10
            return json.dumps({
                "action_rules": [
                    {
                        "action_id": f"action_{offset + index}",
                        "name": f"行动{offset + index}",
                        "cost_action_points": 1,
                        "budget_cost": 0,
                        "allowed_targets": ["villager"],
                        "preconditions": [],
                        "forbidden_conditions": [],
                        "direct_payoff": {},
                        "side_effects": [],
                        "risk_notes": [],
                        "citations": ["query"],
                    }
                    for index in range(1, 7)
                ]
            }, ensure_ascii=False)
        event_offset = {5: 0, 6: 10, 7: 20}[stage]
        payload = {
            "event_outline": [
                {
                    "event_id": f"event_{event_offset + index}",
                    "name": f"事件{event_offset + index}",
                    "day_window": "第1-10天",
                    "trigger_condition": "测试触发",
                    "description": "测试事件",
                    "payoff": {},
                    "citations": ["query"],
                }
                for index in range(1, 6)
            ]
        }
        if stage == 7:
            payload["night_rules"] = ["夜间规则1", "夜间规则2", "夜间规则3"]
            payload["payoff_notes"] = ["结算说明"]
            payload["citations"] = []
        return json.dumps(payload, ensure_ascii=False)

    def request_size_bytes(self, messages, temperature=0.2):
        return 1


class StagedGenerationTests(unittest.TestCase):
    def test_full_generation_calls_one_client_once_per_stage(self) -> None:
        client = FakeStageClient()
        generator = QwenScriptGenerator(client=client)

        script = generator.generate_full("生成基层治理严肃游戏", contexts=[])

        self.assertEqual(len(client.calls), 7)
        self.assertEqual(script.title, "父母官测试剧本")
        self.assertEqual(len(script.npc_seed), 12)
        self.assertEqual(len(script.action_rules), 12)
        self.assertEqual(len(script.event_outline), 15)
        self.assertEqual(len(script.night_rules), 3)


class PABackendScriptClientTests(unittest.TestCase):
    def test_reuses_one_conversation_across_stage_calls(self) -> None:
        class RecordingClient(PABackendScriptClient):
            def __init__(self):
                super().__init__(
                    PABackendConfig(
                        base_url="http://pa.test",
                        supabase_url="http://supabase.test",
                        supabase_key="supabase-key",
                        account="user@test.com",
                        password="password",
                    )
                )
                self.calls = []

            def _post(self, url, payload, token, extra_headers=None):
                self.calls.append({
                    "url": url,
                    "payload": payload,
                    "token": token,
                    "extra_headers": extra_headers or {},
                })
                if url.endswith("/auth/sso/login-password"):
                    return '{"access_token": "token-1", "user": {"id": "user-1"}}'
                if url.endswith("/rest/v1/conversations?select=id"):
                    return '[{"id": "conversation-1"}]'
                return 'event: content\ndata: "{\\"ok\\": true}"\n\n'

        client = RecordingClient()

        first = client.complete([FakeMessage("system", "阶段一"), FakeMessage("user", "{}")])
        second = client.complete([FakeMessage("system", "阶段二"), FakeMessage("user", "{}")])

        self.assertEqual(first, '{"ok": true}')
        self.assertEqual(second, '{"ok": true}')
        agent_calls = [
            call for call in client.calls
            if call["url"].endswith("/agent/os-search/general")
        ]
        conversation_creates = [
            call for call in client.calls
            if call["url"].endswith("/rest/v1/conversations?select=id")
        ]
        self.assertEqual(len(conversation_creates), 1)
        self.assertEqual(len(agent_calls), 2)
        self.assertEqual(agent_calls[0]["payload"]["conversation_id"], "conversation-1")
        self.assertEqual(agent_calls[1]["payload"]["conversation_id"], "conversation-1")


class FakeMessage:
    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content


if __name__ == "__main__":
    unittest.main()
