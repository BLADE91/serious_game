import json
import unittest

from src.generation.script_generator import QwenScriptGenerator
from src.config import PABackendConfig
from src.generation.pa_backend_script_client import PABackendScriptClient


class FakeStageClient:
    """模拟 LLM 客户端，按新 7 阶段格式返回测试数据。"""

    def __init__(self) -> None:
        self.calls = []

    def complete(self, messages, temperature=0.2):
        self.calls.append(messages)
        stage = len(self.calls)
        if stage == 1:
            # 阶段 1：总体设计 + 三幕结构
            return json.dumps({
                "title": "父母官测试剧本",
                "premise": "基层政策执行遇到多方冲突。",
                "player_role": "乡镇干部",
                "core_conflict": "政策目标、群众利益与执行压力冲突。",
                "initial_game_state": {
                    "day": 1, "action_points": 3, "budget_remaining": 8000,
                    "budget_unit": "万元", "signed_households": 0,
                    "total_households": 36, "social_stability_index": 70,
                    "political_credit": 70, "cadre_execution_index": 60,
                },
                "acts": [
                    {"act_number": 1, "title": "开局破冰", "day_range": "第1-15天",
                     "goal": "完成摸底和首轮动员", "description": "进驻初期"},
                    {"act_number": 2, "title": "矛盾升级", "day_range": "第16-50天",
                     "goal": "突破核心矛盾", "description": "矛盾集中爆发期"},
                    {"act_number": 3, "title": "收束结局", "day_range": "第51-90天",
                     "goal": "完成签约或承担后果", "description": "最终冲刺"},
                ],
                "citations": [],
            }, ensure_ascii=False)
        if stage == 2:
            # 阶段 2：NPC 网络
            return json.dumps({
                "npc_seed": [
                    {
                        "npc_id": f"npc_{idx}", "name": f"角色{idx}",
                        "npc_type": "villager" if idx > 3 else "cadre",
                        "group": "测试组", "trust_to_player": 50,
                        "attitude_score": 50, "anxiety_level": 50,
                        "reference_point": 0, "granovetter_threshold": 50,
                        "core_demand_satisfied": False, "signed": False,
                        "known_info": ["测试信息"], "player_promises": [],
                    }
                    for idx in range(1, 13)
                ],
                "npc_relationships": [
                    {"from_npc_id": f"npc_{i}", "to_npc_id": f"npc_{i+1}",
                     "relation_type": "上下级", "strength": 70,
                     "description": f"角色{i}领导角色{i+1}"}
                    for i in range(1, 11)
                ],
            }, ensure_ascii=False)
        if stage in {3, 4, 5, 6}:
            # 阶段 3-6：决策点（每幕/半幕一批）
            offset_map = {3: 0, 4: 10, 5: 20, 6: 30}
            offset = offset_map[stage]
            return json.dumps({
                "decision_points": [
                    {
                        "decision_id": f"dp_{offset + idx}",
                        "title": f"决策{offset + idx}",
                        "day_window": f"第{offset + idx}-{offset + idx + 2}天",
                        "situation": "测试情境描述",
                        "options": [
                            {
                                "option_id": f"opt_{offset + idx}_a",
                                "label": "方案A", "description": "选择方案A",
                                "cost_action_points": 1, "budget_cost": 100,
                                "payoffs": {"global": {"social_stability_index": 5}},
                                "risks": ["风险A"], "citation": "query",
                            },
                            {
                                "option_id": f"opt_{offset + idx}_b",
                                "label": "方案B", "description": "选择方案B",
                                "cost_action_points": 2, "budget_cost": 0,
                                "payoffs": {"global": {"political_credit": 5}},
                                "risks": [], "citation": "query",
                            },
                            {
                                "option_id": f"opt_{offset + idx}_c",
                                "label": "方案C", "description": "选择方案C",
                                "cost_action_points": 1, "budget_cost": 50,
                                "payoffs": {},
                                "risks": ["风险C"], "citation": "query",
                            },
                        ],
                        "affected_npc_ids": [f"npc_{idx}" for idx in range(1, 5)],
                        "trigger_condition": "", "is_critical": (idx == 1),
                        "citations": ["query"],
                    }
                    for idx in range(1, 6)
                ]
            }, ensure_ascii=False)
        # 阶段 7：多结局条件
        return json.dumps({
            "endings": [
                {
                    "ending_id": "end_good", "title": "完美收官",
                    "description": "全部签约且社会稳定。",
                    "conditions": ["signed_households >= 34", "social_stability_index >= 60"],
                    "ending_type": "good",
                },
                {
                    "ending_id": "end_neutral", "title": "勉强过关",
                    "description": "签约达标但留下隐患。",
                    "conditions": ["signed_households >= 28", "political_credit >= 40"],
                    "ending_type": "neutral",
                },
                {
                    "ending_id": "end_bad_1", "title": "资金断裂",
                    "description": "预算耗尽，项目烂尾。",
                    "conditions": ["budget_remaining <= 0"],
                    "ending_type": "bad",
                },
                {
                    "ending_id": "end_bad_2", "title": "民怨爆发",
                    "description": "群体事件导致问责。",
                    "conditions": ["social_stability_index < 30"],
                    "ending_type": "bad",
                },
            ]
        }, ensure_ascii=False)

    def request_size_bytes(self, messages, temperature=0.2):
        return 1


class StagedGenerationTests(unittest.TestCase):
    def test_full_generation_produces_new_structure(self) -> None:
        """完整生成应调用 7 个阶段并产出新结构字段。"""
        client = FakeStageClient()
        generator = QwenScriptGenerator(client=client)

        script = generator.generate_full("生成基层治理严肃游戏", contexts=[])

        self.assertEqual(len(client.calls), 7)
        self.assertEqual(script.title, "父母官测试剧本")
        # NPC 网络
        self.assertEqual(len(script.npc_seed), 12)
        self.assertGreater(len(script.npc_relationships), 0)
        # 三幕结构
        self.assertEqual(len(script.acts), 3)
        self.assertEqual(script.acts[0].act_number, 1)
        self.assertEqual(script.acts[2].act_number, 3)
        # 决策点（4 批 × 5 个 = 20）
        self.assertEqual(len(script.decision_points), 20)
        for dp in script.decision_points:
            self.assertGreaterEqual(len(dp.options), 3)
        # 结局
        self.assertEqual(len(script.endings), 4)
        ending_types = {e.ending_type for e in script.endings}
        self.assertIn("good", ending_types)
        self.assertIn("bad", ending_types)
        # budget_unit
        self.assertEqual(script.initial_game_state.budget_unit, "万元")


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
                        collection_id="collection-1",
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
        self.assertEqual(agent_calls[0]["payload"]["collection_ids"], ["collection-1"])
        self.assertTrue(agent_calls[0]["payload"]["enable_web_search"])

    def test_reads_sse_response_incrementally(self) -> None:
        class FakeResponse:
            def __init__(self, lines):
                self._lines = [line.encode("utf-8") for line in lines]
                self._index = 0

            def readline(self):
                if self._index >= len(self._lines):
                    return b""
                line = self._lines[self._index]
                self._index += 1
                return line

        client = PABackendScriptClient(
            PABackendConfig(
                base_url="http://pa.test",
                supabase_url="http://supabase.test",
                supabase_key="supabase-key",
                account="user@test.com",
                password="password",
            )
        )
        counts = []
        response = FakeResponse([
            "event: content\n",
            'data: "foo"\n',
            "\n",
            "event: content\n",
            'data: "bar"\n',
            "\n",
        ])

        content = client._read_sse_response(response, counts.append)

        self.assertEqual(content, "foobar")
        self.assertEqual(counts, [3, 6])

    def test_reports_non_content_sse_events(self) -> None:
        class FakeResponse:
            def __init__(self, lines):
                self._lines = [line.encode("utf-8") for line in lines]
                self._index = 0

            def readline(self):
                if self._index >= len(self._lines):
                    return b""
                line = self._lines[self._index]
                self._index += 1
                return line

        client = PABackendScriptClient(
            PABackendConfig(
                base_url="http://pa.test",
                supabase_url="http://supabase.test",
                supabase_key="supabase-key",
                account="user@test.com",
                password="password",
            )
        )
        progress = []
        response = FakeResponse([
            "event: thought\n",
            'data: {"stage": "search", "label": "正在检索知识库"}\n',
            "\n",
            "event: tool_call\n",
            'data: {"name": "os_search", "arguments": {"query": "生态搬迁"}}\n',
            "\n",
            "event: tool_result\n",
            'data: {"name": "os_search", "preview": "命中 3 条", "is_error": false}\n',
            "\n",
            "event: usage_update\n",
            'data: {"billing_mode": "react_agent_entry", "current_chain_tokens": 1200}\n',
            "\n",
            "event: content\n",
            'data: "done"\n',
            "\n",
        ])

        content = client._read_sse_response(
            response,
            lambda chars, event=None: progress.append((chars, event)),
        )

        self.assertEqual(content, "done")
        event_names = [event.get("event") for _, event in progress if event]
        self.assertIn("thought", event_names)
        self.assertIn("tool_call", event_names)
        self.assertIn("tool_result", event_names)
        self.assertIn("usage_update", event_names)
        self.assertIn("content", event_names)
        messages = [event.get("message", "") for _, event in progress if event]
        self.assertTrue(any("正在检索知识库" in message for message in messages))
        self.assertTrue(any("os_search" in message for message in messages))

    def test_retries_empty_stage_with_new_conversation(self) -> None:
        class RetryClient(PABackendScriptClient):
            def __init__(self):
                super().__init__(PABackendConfig(
                    base_url="http://pa.test",
                    supabase_url="http://supabase.test",
                    supabase_key="supabase-key",
                    account="user@test.com",
                    password="password",
                    max_stage_retries=2,
                    retry_backoff_seconds=0,
                ))
                self.agent_calls = 0
                self.conversation_creates = 0

            def _post(self, url, payload, token, extra_headers=None):
                if url.endswith("/auth/sso/login-password"):
                    return '{"access_token": "token-1", "user": {"id": "user-1"}}'
                if url.endswith("/rest/v1/conversations?select=id"):
                    self.conversation_creates += 1
                    return json.dumps([{
                        "id": f"conversation-{self.conversation_creates}",
                    }])
                self.agent_calls += 1
                if self.agent_calls < 3:
                    return "event: done\ndata: {}\n\n"
                return 'event: content\ndata: {"content": "recovered"}\n\n'

        client = RetryClient()
        content = client.complete([FakeMessage("user", "retry")])

        self.assertEqual(content, "recovered")
        self.assertEqual(client.agent_calls, 3)
        self.assertEqual(client.conversation_creates, 3)

    def test_empty_stage_error_includes_event_summary(self) -> None:
        class EmptyClient(PABackendScriptClient):
            def __init__(self):
                super().__init__(PABackendConfig(
                    base_url="http://pa.test",
                    supabase_url="http://supabase.test",
                    supabase_key="supabase-key",
                    account="user@test.com",
                    password="password",
                    max_stage_retries=0,
                ))

            def _post(self, url, payload, token, extra_headers=None):
                if url.endswith("/auth/sso/login-password"):
                    return '{"access_token": "token-1", "user": {"id": "user-1"}}'
                if url.endswith("/rest/v1/conversations?select=id"):
                    return '[{"id": "conversation-1"}]'
                return 'event: error\ndata: "backend failed"\n\nevent: done\ndata: {}\n\n'

        with self.assertRaisesRegex(Exception, "error.*done"):
            EmptyClient().complete([FakeMessage("user", "fail")])


class FakeMessage:
    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content


if __name__ == "__main__":
    unittest.main()
