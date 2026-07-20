from __future__ import annotations

import unittest

from terminal_client.app import TerminalApp
from terminal_client.api_client import ApiError


def visible_state(
    *, version: int, day: int, pending: dict | None, origin_id: str = "technical"
) -> dict:
    return {
        "session_id": "game_m1_test",
        "state_version": version,
        "status": "active",
        "story": {
            "day": day,
            "chapter": 1,
            "cost_tier": "normal",
            "beat_id": "beat",
            "origin": {"origin_id": origin_id, "title": "技术派"},
        },
        "ledger": {
            "days_left": 91 - day,
            "action_points": {"remaining": 8, "daily_cap": 8},
            "signed_households": {"signed": 0, "total": 36},
            "budget": {"remaining": 8000, "unit": "万元"},
        },
        "indicators": {
            "public_trust": "观望",
            "social_stability": "绷紧",
        },
        "pending_decision": pending,
        "visible_events": [],
    }


RECEPTION_PENDING = {
    "decision_id": "ev1_01_reception_bag",
    "title": "廉政试炼·接风袋",
    "text": "袋子不重，你能摸出里头的棱角。",
    "options": [
        {"option_id": "a_reject_on_site", "text": "当场把袋子推回去。"},
        {"option_id": "b_file_with_discipline", "text": "送交县纪委备案。"},
    ],
}

TASKFORCE_PENDING = {
    "decision_id": "dp1_01_taskforce_faction_map",
    "title": "DP1-01·组建专班与摸派系图",
    "text": "请做出决定。",
    "options": [
        {"option_id": "a_rely_on_local_team", "text": "倚重老班底。"},
        {"option_id": "b_build_independent_team", "text": "自建独立专班。"},
        {"option_id": "c_public_rules_covert_check", "text": "暗地核实。"},
    ],
}


class FakeApi:
    def __init__(self) -> None:
        self.phase = 0
        self.selected_options: list[str] = []
        self.conversation_active = False

    @staticmethod
    def new_key(prefix: str) -> str:
        return f"test-{prefix}-key"

    def new_session(self, *, origin_id: str) -> dict:
        assert origin_id == "technical"
        return visible_state(version=1, day=1, pending=RECEPTION_PENDING)

    def get_view(self, session_id: str, *, after: int) -> dict:
        assert session_id == "game_m1_test"
        definitions = {
            0: (1, 1, RECEPTION_PENDING, "你叫李致远，到云溪的头一天。"),
            1: (2, 1, None, "袋子被推回桌上。"),
            2: (3, 2, TASKFORCE_PENDING, "第二天上午九点。"),
            3: (4, 2, None, "吴秀英拎着一篮子青菜走来。"),
            4: (5, 2, None, "吴秀英：谁的话在谁面前好使。"),
            5: (6, 3, None, "M1 首条完整垂直切片已经收束。"),
        }
        version, day, pending, text = definitions[self.phase]
        commands = {
            "can_choose": pending is not None,
            "can_act": self.phase in {3, 4, 5},
            "can_talk": self.phase in {3, 5},
            "can_end_day": self.phase in {1, 4},
        }
        cursor = self.phase + 1
        state = visible_state(version=version, day=day, pending=pending)
        state["active_conversation"] = (
            {
                "conversation_id": "conv_test_001",
                "opportunity_id": "opp_d02_wu_xiuying_first_talk",
                "npc_id": "npc_wu_xiuying",
                "turn_count": 0,
            }
            if self.conversation_active else None
        )
        return {
            "state": state,
            "feed": {
                "cursor": cursor,
                "items": (
                    [{"cursor": cursor, "kind": "narration", "text": text}]
                    if after < cursor
                    else []
                ),
            },
            "commands": commands,
        }

    def submit_decision(self, session_id: str, **payload) -> dict:
        assert session_id == "game_m1_test"
        self.selected_options.append(payload["option_id"])
        if self.phase == 0:
            self.phase = 1
        elif self.phase == 2:
            self.phase = 3
        else:
            raise AssertionError("unexpected decision phase")
        return {"status": "processing", "poll_after_ms": 1}

    def get_operation(self, session_id: str, client_action_id: str) -> dict:
        assert session_id == "game_m1_test"
        assert client_action_id == "test-decision-key"
        version = 2 if self.phase == 1 else 4
        return {
            "status": "succeeded",
            "response": {"state_version": version, "narrative": "hidden duplicate"},
        }

    def end_day(self, session_id: str, **payload) -> dict:
        assert session_id == "game_m1_test"
        if self.phase == 1:
            assert payload["state_version"] == 2
            self.phase = 2
            return {"state_version": 3}
        if self.phase == 4:
            assert payload["state_version"] == 5
            self.phase = 5
            return {"state_version": 6}
        raise AssertionError("unexpected end-day phase")

    def get_opportunities(self, session_id: str) -> dict:
        assert session_id == "game_m1_test"
        if self.phase == 3:
            items = [{
                "opportunity_id": "opp_d02_wu_xiuying_first_talk",
                "npc_id": "npc_wu_xiuying",
                "action_id": "home_visit",
                "cost_action_points": 1,
                "conversation_active": self.conversation_active,
                "conversation_id": "conv_test_001" if self.conversation_active else None,
            }]
        elif self.phase == 5:
            items = [{
                "opportunity_id": "opp_d03_zhou_dashan_first_talk",
                "npc_id": "npc_zhou_dashan",
                "action_id": "heart_to_heart",
                "cost_action_points": 2,
            }]
        else:
            items = []
        return {
            "state_version": {3: 4, 5: 6}.get(self.phase, 1),
            "opportunities": items,
        }

    def start_conversation(self, session_id: str, **payload) -> dict:
        assert self.phase == 3
        self.conversation_active = True
        return {
            "state_version": 4,
            "status": "succeeded",
            "conversation": {
                "conversation_id": "conv_test_001", "status": "active",
            },
        }

    def submit_free_text(self, session_id: str, **payload) -> dict:
        assert session_id == "game_m1_test"
        assert self.phase == 3
        assert payload["target_npc_id"] == "npc_wu_xiuying"
        assert payload["conversation_id"] == "conv_test_001"
        self.phase = 4
        return {"state_version": 5, "status": "succeeded"}

    def end_conversation(self, session_id: str, **payload) -> dict:
        assert payload["conversation_id"] == "conv_test_001"
        self.conversation_active = False
        return {
            "state_version": 5, "status": "succeeded",
            "conversation": {"conversation_id": "conv_test_001", "status": "ended"},
        }

    def get_map(self, session_id: str) -> dict:
        return {"story_day": 1, "locations": [{
            "location_id": "loc_liulin_village", "name": "柳林村",
            "visual_state": "known", "opportunity_ids": [],
        }]}

    def get_review(self, session_id: str) -> dict:
        return {"status": "active", "decision_timeline": [],
                "night_timeline": [], "visible_events": [], "ending": None}

    def get_package_validation(self) -> dict:
        return {"valid": True, "package_id": "pkg_backend_dev_v1",
                "counts": {"story_days": 90, "decision_catalog": 62,
                           "event_catalog": 14, "main_endings": 24,
                           "sub_endings": 95, "runtime_decisions": 76}}


class TerminalAppTests(unittest.TestCase):
    def test_default_menu_can_register_choose_origin_and_resolve_decision(self) -> None:
        class MenuApi(FakeApi):
            def readiness(self):
                return {"authentication_required": True}

            def me(self):
                raise ApiError("未登录", code="AUTHENTICATION_REQUIRED", status=401)

            def register(self, username, password):
                assert username == "player"
                assert password == "pass1234"
                return {"account_id": "acct_player"}

            def get_latest_active(self):
                raise ApiError("没有存档", code="NOT_FOUND", status=404)

            def get_origins(self):
                return {"origins": [{
                    "origin_id": "technical", "title": "技术派", "description": "测试出身",
                }]}

        menu_inputs = iter(["2", "player", "1", "1", "1", "0"])
        passwords = iter(["pass1234", "pass1234"])
        output: list[str] = []
        prompts: list[str] = []
        api = MenuApi()
        app = TerminalApp(
            api, menu_mode=True,
            input_fn=lambda prompt: (prompts.append(prompt), next(menu_inputs))[1],
            password_fn=lambda _prompt: next(passwords),
            output_fn=output.append, sleep_fn=lambda _seconds: None,
        )

        self.assertEqual(0, app.run())
        self.assertEqual(["a_reject_on_site"], api.selected_options)
        text = "\n".join(output)
        self.assertIn("账号入口", text)
        self.assertIn("请选择开局出身", text)
        self.assertTrue(any("请选择序号" in prompt for prompt in prompts))
        self.assertNotIn("输入 choose", text)

    def test_menu_handles_sorting_and_allocation_without_commands(self) -> None:
        sorting = {
            "input_kind": "sorting", "title": "排序",
            "options": [
                {"option_id": "a", "text": "甲", "available": True},
                {"option_id": "b", "text": "乙", "available": True},
                {"option_id": "c", "text": "丙", "available": True},
            ],
        }
        order_inputs = iter(["2", "1", "1"])
        app = TerminalApp(FakeApi(), input_fn=lambda _prompt: next(order_inputs))
        captured_order = []
        app._order = lambda values: captured_order.extend(values)
        app._menu_decision(sorting)
        self.assertEqual(["b", "a", "c"], captured_order)

        allocation = {
            "input_kind": "allocation", "title": "分配",
            "input_schema": {
                "total": 10, "unit": "万元", "fields": ["a", "b", "c", "d"],
                "labels": {"a": "甲", "b": "乙", "c": "丙", "d": "丁"},
            },
        }
        allocation_inputs = iter(["1", "1", "2", "3", "1", "1", "2", "3", "4"])
        app = TerminalApp(FakeApi(), input_fn=lambda _prompt: next(allocation_inputs))
        captured_allocation = []
        app._allocate = lambda values: captured_allocation.extend(values)
        app._menu_decision(allocation)
        self.assertEqual(["1", "2", "3", "4"], captured_allocation)

    def test_menu_selects_talk_and_action_by_number(self) -> None:
        class SelectionApi(FakeApi):
            def get_opportunities(self, session_id):
                return {"opportunities": [{
                    "opportunity_id": "opp_1", "npc_id": "npc_1",
                    "npc_name": "吴秀英", "npc_title": "村民代表，退休教师",
                    "npc_introduction": "吴秀英是柳林村有威望的退休教师。",
                    "conversation_context": "剧情后续交谈，接触方式：入户走访",
                    "cost_action_points": 1,
                }]}

            def get_actions(self, session_id):
                return {"actions": [{
                    "action_id": "visit", "name": "走访", "available": True,
                    "cost_action_points": 1, "opportunity_ids": ["opp_1"],
                }]}

        api = SelectionApi()
        talk_inputs = iter(["1", "1", "1", "请告诉我情况", "2"])
        output: list[str] = []
        app = TerminalApp(
            api, input_fn=lambda _prompt: next(talk_inputs), output_fn=output.append
        )
        app.session_id = "game_m1_test"
        captured_talk = []
        captured_end = []
        app._start_conversation = lambda _opportunity: {
            "conversation": {"conversation_id": "conv_test_001", "status": "active"}
        }
        def capture_talk(opportunity_id, conversation_id, text):
            captured_talk.extend((opportunity_id, conversation_id, text))
            return {"conversation": {"status": "active"}}
        app._talk = capture_talk
        app._end_conversation = lambda conversation_id: captured_end.append(conversation_id)
        app._menu_talk()
        self.assertEqual(
            ["opp_1", "conv_test_001", "请告诉我情况"], captured_talk
        )
        self.assertEqual(["conv_test_001"], captured_end)
        menu_text = "\n".join(output)
        self.assertIn("吴秀英｜村民代表，退休教师", menu_text)
        self.assertIn("人物简介：吴秀英是柳林村有威望的退休教师。", menu_text)
        self.assertIn("本次接触：剧情后续交谈，接触方式：入户走访", menu_text)

        action_inputs = iter(["1"])
        app = TerminalApp(api, input_fn=lambda _prompt: next(action_inputs))
        app.session_id = "game_m1_test"
        captured_action = []
        app._do = lambda action_id, opportunity_id: captured_action.extend((action_id, opportunity_id))
        app._menu_action()
        self.assertEqual(["visit", "opp_1"], captured_action)

    def test_registration_reprompts_until_password_is_valid_and_confirmed(self) -> None:
        class AuthApi:
            def __init__(self) -> None:
                self.registered = None

            def register(self, username, password):
                self.registered = (username, password)
                return {"account_id": "acct_local"}

            def get_latest_active(self):
                raise ApiError("没有存档", code="NOT_FOUND", status=404)

            def get_origins(self):
                return {"origins": [{
                    "origin_id": "technical", "title": "技术派", "description": "测试",
                }]}

        passwords = iter([
            "short", "validpass", "different", "validpass", "validpass",
        ])
        output: list[str] = []
        api = AuthApi()
        app = TerminalApp(
            api, output_fn=output.append,
            password_fn=lambda _prompt: next(passwords),
        )

        app.handle("register player")

        self.assertEqual(("player", "validpass"), api.registered)
        text = "\n".join(output)
        self.assertIn("密码少于 8 个字符", text)
        self.assertIn("两次输入的密码不一致", text)
        self.assertIn("下一步：输入 new <origin_id>", text)

    def test_command_prompt_always_contains_contextual_guidance(self) -> None:
        app = TerminalApp(FakeApi())
        app.authentication_required = True
        self.assertIn("register/login/help", app._command_prompt())
        app.authenticated = True
        self.assertIn("origins/new/continue/help", app._command_prompt())
        app.session_id = "game_m1_test"
        app.state = visible_state(version=1, day=2, pending=None)
        self.assertIn("D2", app._command_prompt())

    def test_talk_timeout_recovers_same_operation_without_duplicate_render(self) -> None:
        class TimeoutRecoveryApi:
            def __init__(self) -> None:
                self.submit_count = 0
                self.operation_count = 0

            @staticmethod
            def new_key(prefix):
                return f"timeout-{prefix}-key"

            def get_opportunities(self, session_id):
                return {
                    "state_version": 5,
                    "opportunities": [{
                        "opportunity_id": "opp_wu",
                        "npc_id": "npc_wu",
                        "npc_name": "吴秀英",
                        "conversation_active": True,
                        "conversation_id": "conv_wu",
                    }],
                }

            def submit_free_text(self, session_id, **payload):
                self.submit_count += 1
                self.client_action_id = payload["client_action_id"]
                raise ApiError("请求后端超时", code="CLIENT_TIMEOUT")

            def get_operation(self, session_id, client_action_id):
                self.operation_count += 1
                assert client_action_id == self.client_action_id
                return {
                    "status": "succeeded",
                    "response": {
                        "status": "succeeded",
                        "state_version": 6,
                        "conversation": {
                            "conversation_id": "conv_wu",
                            "status": "ended",
                            "ended_by": "npc",
                        },
                    },
                }

            def get_view(self, session_id, *, after):
                state = visible_state(version=6, day=2, pending=None)
                state["active_conversation"] = None
                items = [] if after >= 2 else [
                    {
                        "cursor": 1,
                        "kind": "player_dialogue",
                        "speaker": "李致远",
                        "text": "请给我交个底。",
                    },
                    {
                        "cursor": 2,
                        "kind": "dialogue",
                        "speaker": "吴秀英",
                        "text": "我只认公道二字。",
                    },
                ]
                return {
                    "state": state,
                    "feed": {"cursor": 2, "items": items},
                    "commands": {"can_talk": False, "can_act": True},
                }

        api = TimeoutRecoveryApi()
        output: list[str] = []
        app = TerminalApp(
            api,
            output_fn=output.append,
            sleep_fn=lambda _seconds: None,
        )
        app.session_id = "game_timeout"
        app.state_version = 5

        result = app._talk("opp_wu", "conv_wu", "请给我交个底。")
        app._safe_refresh()

        self.assertEqual("ended", result["conversation"]["status"])
        self.assertEqual(1, api.submit_count)
        self.assertEqual(1, api.operation_count)
        self.assertIsNone(app.pending_operation_id)
        self.assertIsNone(app.state.get("active_conversation"))
        rendered = "\n".join(output)
        self.assertIn("请勿重复输入", rendered)
        self.assertEqual(1, rendered.count("李致远：请给我交个底。"))
        self.assertEqual(1, rendered.count("吴秀英：我只认公道二字。"))

    def test_scripted_m1_flow_reaches_d3_and_next_opportunity(self) -> None:
        commands = iter([
            "new technical",
            "choose A",
            "end",
            "choose C",
            "talk opp_d02_wu_xiuying_first_talk 我想先听您说真话",
            "leave",
            "end",
            "opportunities",
            "quit",
        ])
        output: list[str] = []
        api = FakeApi()
        app = TerminalApp(
            api,
            input_fn=lambda _prompt: next(commands),
            output_fn=output.append,
            sleep_fn=lambda _seconds: None,
        )

        self.assertEqual(0, app.run())
        self.assertEqual(
            ["a_reject_on_site", "c_public_rules_covert_check"],
            api.selected_options,
        )
        self.assertEqual(3, app.state["story"]["day"])
        text = "\n".join(output)
        self.assertIn("你叫李致远", text)
        self.assertIn("谁的话在谁面前好使", text)
        self.assertIn("M1 首条完整垂直切片已经收束", text)
        self.assertIn("opp_d03_zhou_dashan_first_talk", text)
        self.assertNotIn("hidden duplicate", text)

    def test_command_requires_loaded_session(self) -> None:
        output: list[str] = []
        app = TerminalApp(FakeApi(), output_fn=output.append)
        with self.assertRaisesRegex(ValueError, "尚未载入游戏"):
            app.handle("end")

    def test_m2_map_review_and_validation_commands(self) -> None:
        commands = iter(["new technical", "map", "review", "validate", "quit"])
        output: list[str] = []
        app = TerminalApp(
            FakeApi(), input_fn=lambda _prompt: next(commands), output_fn=output.append
        )
        self.assertEqual(0, app.run())
        text = "\n".join(output)
        self.assertIn("柳林村", text)
        self.assertIn("剧本包校验：通过", text)
        self.assertIn("结局 24/95", text)


if __name__ == "__main__":
    unittest.main()
