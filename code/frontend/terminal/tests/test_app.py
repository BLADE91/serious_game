from __future__ import annotations

import unittest

from terminal_client.app import TerminalApp
from terminal_client.api_client import ApiError
from terminal_client.renderer import render_state


def visible_state(
    *, version: int, day: int, pending: dict | None, origin_id: str = "mayor"
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
            "origin": {"origin_id": origin_id, "title": "云溪县县长"},
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

    def new_session(self) -> dict:
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

    def get_night_dialogues(self, session_id: str) -> dict:
        return {
            "session_id": session_id,
            "nights": [{
                "story_day": 29,
                "contact_selections": [{
                    "npc_id": "npc_qian_wei",
                    "model_id": "model-qian",
                    "contact_ids": ["npc_zhao_jianguo"],
                    "rationale": "需要核对口径。",
                    "accepted": True,
                }],
                "contact_responses": [{
                    "initiator_npc_id": "npc_qian_wei",
                    "invited_npc_id": "npc_zhao_jianguo",
                    "model_id": "model-zhao",
                    "response": "accept",
                    "rationale": "需要听听对方准备怎么说。",
                    "accepted": True,
                }],
                "agent_exchanges": [{
                    "scene_id": "night_d29_qian_zhao_private_room",
                    "group_index": 1,
                    "participant_ids": [
                        "npc_qian_wei", "npc_zhao_jianguo"
                    ],
                    "transcript": [{
                        "round": 1,
                        "speaker_name": "钱伟",
                        "model_id": "model-qian",
                        "dialogue": "这件事不能再有两套说法。",
                    }],
                    "action_proposals": [{
                        "npc_id": "npc_qian_wei",
                        "action_id": "night_unify_story",
                        "accepted": True,
                        "rationale": "继续合作更安全。",
                    }],
                    "executed_action_ids": ["night_unify_story"],
                }],
            }],
        }

    def reply_group_conversation(
        self, session_id: str, *, state_version: int, player_text: str
    ) -> dict:
        state = visible_state(version=state_version + 1, day=30, pending=None)
        state["active_group_conversation"] = {
            "conversation_id": "group_test",
            "phase": "resolved",
            "participant_states": {
                "npc_zhao_jianguo": {
                    "status": "settled",
                    "public_summary": "暂时接受书面责任安排",
                },
                "npc_sun_qiang": {
                    "status": "settled",
                    "public_summary": "等待次日执行口径",
                },
            },
            "closure_text": "今晚先谈到这里，明早按公开节点核对。",
        }
        return {
            "state_version": state_version + 1,
            "phase": "resolved",
            "turn_dialogues": [
                {
                    "npc_id": "npc_zhao_jianguo",
                    "npc_name": "赵建国",
                    "model_id": "model-zhao",
                    "text": "这项责任需要在今天明确下来。",
                },
                {
                    "npc_id": "npc_sun_qiang",
                    "npc_name": "孙强",
                    "model_id": "model-sun",
                    "text": "基层需要一份可以照着执行的书面口径。",
                },
            ],
            "visible_state": state,
        }

    def finish_group_conversation(
        self, session_id: str, *, state_version: int
    ) -> dict:
        state = visible_state(version=state_version + 1, day=30, pending=None)
        state["active_group_conversation"] = None
        return {
            "state_version": state_version + 1,
            "visible_state": state,
        }

    def get_package_validation(self) -> dict:
        return {"valid": True, "package_id": "pkg_backend_dev_v1",
                "counts": {"story_days": 90, "decision_catalog": 62,
                           "event_catalog": 14, "main_endings": 24,
                           "sub_endings": 95, "runtime_decisions": 76}}

    def get_knowledge(self, session_id: str) -> dict:
        return {
            "state_version": 4,
            "facts": [{
                "fact_id": "fact_map", "title": "柳林村宗族权力图",
                "text": "周姓11户集中住在村中心。", "source_label": "吴秀英交谈",
                "use_hint": "可用于判断宗族与散姓关系。",
            }],
            "clues": [], "evidence": [],
        }

    def get_desk(self, session_id: str) -> dict:
        return {
            "state_version": 4,
            "mission": {"title": "任务书", "summary": "推进搬迁。", "hard_constraints": []},
            "dossiers": [],
            "compensation_policy": {
                "title": "补偿政策底册", "status": "参数待补全", "funding": [],
                "principles": [], "numeric_guardrail": "未配置数字不得编造。",
                "current_budget": {"initial": 8000, "remaining": 8000, "deducted": 0, "unit": "万元"},
            },
            "authorities": [], "tool_categories": [], "tools": [],
        }


class TerminalAppTests(unittest.TestCase):
    def test_forced_group_conversation_renders_each_npc_reply(self) -> None:
        output: list[str] = []
        app = TerminalApp(FakeApi(), output_fn=output.append)
        app.session_id = "game_m1_test"
        app.state_version = 10
        app.state = visible_state(version=10, day=30, pending=None)
        app.state["active_group_conversation"] = {
            "conversation_id": "group_test",
            "conversation_type": "cadre_meeting",
            "participant_ids": ["npc_zhao_jianguo", "npc_sun_qiang"],
            "agenda": "汇报基层材料风险",
            "demands": ["明确责任"],
            "phase": "active",
            "transcript": [],
        }

        app._reply_group_conversation("我会今天明确书面责任。")

        text = "\n".join(output)
        self.assertIn("赵建国 [model-zhao]：这项责任需要", text)
        self.assertIn("孙强 [model-sun]：基层需要", text)
        self.assertIn("今晚先谈到这里", text)
        self.assertIn("暂时接受书面责任安排", text)
        self.assertNotIn("轮", text)

    def test_resolved_group_conversation_requires_player_finish_confirmation(self) -> None:
        output: list[str] = []
        app = TerminalApp(
            FakeApi(),
            input_fn=lambda _prompt: "1",
            output_fn=output.append,
        )
        app.session_id = "game_m1_test"
        app.state_version = 11
        app.state = visible_state(version=11, day=30, pending=None)
        app.state["active_group_conversation"] = {
            "conversation_id": "group_test",
            "conversation_type": "cadre_meeting",
            "participant_ids": ["npc_zhao_jianguo", "npc_sun_qiang"],
            "agenda": "汇报基层材料风险",
            "phase": "resolved",
            "participant_states": {},
            "transcript": [],
        }

        app._menu_group_conversation(app.state["active_group_conversation"])

        self.assertEqual(12, app.state_version)
        self.assertIsNone(app.state["active_group_conversation"])
        self.assertIn("夜间会谈已经归档", "\n".join(output))

    def test_signed_review_result_does_not_call_a_second_sign_endpoint(self) -> None:
        output: list[str] = []
        app = TerminalApp(FakeApi(), output_fn=output.append)
        app.state_version = 10

        completed = app._handle_contract_review_result({
            "state_version": 11,
            "contract": {
                "status": "signed",
                "review_decision": "accept",
                "review_reason": "条款已接受并正式签署。",
                "resource_hold_status": "已签署并占用资源，尚未支付",
            },
        })

        self.assertTrue(completed)
        self.assertEqual(11, app.state_version)
        self.assertIn("合同签署成功", "\n".join(output))
    def test_night_dialogue_debug_view_renders_contacts_transcript_and_action(self) -> None:
        output: list[str] = []
        app = TerminalApp(FakeApi(), output_fn=output.append)
        app.session_id = "game_m1_test"
        app.show_night_dialogues = True

        app._show_new_night_dialogues(force=True)

        text = "\n".join(output)
        self.assertIn("D29 夜间联系人选择", text)
        self.assertIn("npc_qian_wei [model-qian] → npc_zhao_jianguo", text)
        self.assertIn(
            "npc_qian_wei → npc_zhao_jianguo [model-zhao]：接受",
            text,
        )
        self.assertIn("钱伟 [model-qian]：这件事不能再有两套说法", text)
        self.assertIn("night_unify_story｜通过", text)
        self.assertIn("最终执行：night_unify_story", text)

    def test_d75_locked_state_shows_signing_batches(self) -> None:
        state = visible_state(version=75, day=76, pending=None)
        state["ledger"]["signed_households"]["batches"] = {
            "roster_locked": True,
            "first_batch": 24,
            "acceptance_confirmed": 3,
            "unsigned": 9,
        }
        rendered = "\n".join(render_state(state))
        self.assertIn("D75 首批 24 户", rendered)
        self.assertIn("验收期确认 3 户", rendered)
        self.assertIn("尚未签署 9 户", rendered)

    def test_default_menu_can_register_start_game_and_resolve_decision(self) -> None:
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
        self.assertIn("是否观看 NPC 夜间对话", text)
        self.assertNotIn("请选择开局出身", text)
        self.assertTrue(any("请选择序号" in prompt for prompt in prompts))
        self.assertNotIn("输入 choose", text)

    def test_menu_does_not_retry_incompatible_latest_save_forever(self) -> None:
        class IncompatibleSaveApi(FakeApi):
            def __init__(self) -> None:
                super().__init__()
                self.latest_calls = 0

            def get_latest_active(self):
                self.latest_calls += 1
                raise ApiError(
                    "游戏锁定的剧本包版本或内容哈希不匹配",
                    code="SESSION_CONTENT_UNAVAILABLE",
                    status=503,
                )

        menu_inputs = iter(["1", "0"])
        output: list[str] = []
        api = IncompatibleSaveApi()
        app = TerminalApp(
            api,
            menu_mode=True,
            input_fn=lambda _prompt: next(menu_inputs),
            output_fn=output.append,
            sleep_fn=lambda _seconds: None,
        )

        self.assertEqual(0, app.run())
        self.assertEqual(1, api.latest_calls)
        text = "\n".join(output)
        self.assertIn("旧存档不可继续", text)
        self.assertIn("旧存档仍会保留", text)
        self.assertNotIn("已刷新服务端权威状态", text)

    def test_incompatible_loaded_session_returns_to_game_entry(self) -> None:
        output: list[str] = []
        app = TerminalApp(FakeApi(), output_fn=output.append)
        app.session_id = "game_old"
        app.state_version = 23
        app.pending_operation_id = "operation_old"
        app.state = visible_state(version=23, day=12, pending=None)
        app.commands = {"can_end_day": True}

        app._handle_unavailable_session()

        self.assertIsNone(app.session_id)
        self.assertIsNone(app.state_version)
        self.assertIsNone(app.pending_operation_id)
        self.assertEqual({}, app.state)
        self.assertEqual({}, app.commands)
        self.assertIn("开始新游戏", "\n".join(output))

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

    def test_meeting_participants_repeat_until_zero_finishes(self) -> None:
        people = [
            {"target_id": "npc_a", "label": "甲"},
            {"target_id": "npc_b", "label": "乙"},
            {"target_id": "npc_c", "label": "丙"},
        ]
        inputs = iter(["1", "2", "0"])
        output: list[str] = []
        app = TerminalApp(
            FakeApi(),
            input_fn=lambda _prompt: next(inputs),
            output_fn=output.append,
        )

        selected = app._select_meeting_participants(people, [])

        self.assertEqual(["npc_a", "npc_c"], selected)
        self.assertNotIn("至少需要2名", "\n".join(output))

    def test_meeting_participants_cannot_finish_below_minimum(self) -> None:
        people = [
            {"target_id": "npc_a", "label": "甲"},
            {"target_id": "npc_b", "label": "乙"},
        ]
        inputs = iter(["0", "1", "0", "1"])
        output: list[str] = []
        app = TerminalApp(
            FakeApi(),
            input_fn=lambda _prompt: next(inputs),
            output_fn=output.append,
        )

        selected = app._select_meeting_participants(people, [])

        self.assertEqual(["npc_a", "npc_b"], selected)
        self.assertIn("至少需要2名", "\n".join(output))

    def test_governance_codes_have_chinese_terminal_labels(self) -> None:
        self.assertEqual(
            {
                "工作实施通知", "医疗保障文件", "迁坟或祠堂事项批复",
                "补偿方案调整文件", "听证通知", "调查通知",
            },
            set(TerminalApp.DOCUMENT_TYPE_LABELS.values()),
        )
        self.assertEqual(
            "历史道路旧案未结",
            TerminalApp.OWNERSHIP_STATUS_LABELS["old_road_case_pending"],
        )

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
                return {"state_version": 1, "actions": [{
                    "action_id": "visit", "name": "走访", "available": True,
                    "cost_action_points": 1, "execution_mode": "resource_action",
                    "direct_budget_cost": 0,
                }]}

        api = SelectionApi()
        talk_inputs = iter(["1", "1", "1", "请告诉我情况", "3"])
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
        app._run_resource_action = lambda item: captured_action.append(item["action_id"])
        app._menu_action()
        self.assertEqual(["visit"], captured_action)

    def test_menu_routes_governance_action_and_can_cancel_active_flow(self) -> None:
        class GovernanceApi(FakeApi):
            def get_actions(self, session_id):
                return {"state_version": 2, "actions": [{
                    "action_id": "household_visit",
                    "name": "入户走访",
                    "available": True,
                    "cost": 1,
                    "execution_mode": "governance",
                }]}

            def get_governance(self, session_id):
                return {
                    "state_version": 3,
                    "resources": {"cash_ledger": {}},
                    "governance_actions": [{
                        "action_instance_id": "govact_1",
                        "action_kind": "household_visit",
                        "topic": "核实搬迁诉求",
                        "status": "active",
                    }],
                }

            def cancel_governance_action(
                self, session_id: str, action_instance_id: str, **payload
            ):
                self.cancel_request = (session_id, action_instance_id, payload)
                return {"state_version": 4, "action": {"status": "cancelled"}}

        api = GovernanceApi()
        app = TerminalApp(api, input_fn=lambda _prompt: "1")
        app.session_id = "game_1"
        captured: list[str] = []
        app._run_governance_action = lambda item: captured.append(item["action_id"])

        app._menu_action()
        self.assertEqual(["household_visit"], captured)

        app._menu_governance()
        self.assertEqual(("game_1", "govact_1"), api.cancel_request[:2])
        self.assertEqual(3, api.cancel_request[2]["state_version"])
        self.assertEqual(4, app.state_version)

    def test_knowledge_displays_body_source_and_use(self) -> None:
        output: list[str] = []
        app = TerminalApp(FakeApi(), output_fn=output.append)
        app.session_id = "game_m1_test"
        app._knowledge()
        text = "\n".join(output)
        self.assertIn("内容：周姓11户集中住在村中心", text)
        self.assertIn("来源：吴秀英交谈", text)
        self.assertIn("可用于：可用于判断宗族与散姓关系", text)

    def test_registration_reprompts_until_password_is_valid_and_confirmed(self) -> None:
        class AuthApi:
            def __init__(self) -> None:
                self.registered = None

            def register(self, username, password):
                self.registered = (username, password)
                return {"account_id": "acct_local"}

            def get_latest_active(self):
                raise ApiError("没有存档", code="NOT_FOUND", status=404)

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
        self.assertIn("下一步：输入 new 开始新游戏", text)

    def test_command_prompt_always_contains_contextual_guidance(self) -> None:
        app = TerminalApp(FakeApi())
        app.authentication_required = True
        self.assertIn("register/login/help", app._command_prompt())
        app.authenticated = True
        self.assertIn("new/continue/help", app._command_prompt())
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
            "new",
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
        self.assertIn("npc_zhou_dashan", text)
        self.assertNotIn("hidden duplicate", text)

    def test_command_requires_loaded_session(self) -> None:
        output: list[str] = []
        app = TerminalApp(FakeApi(), output_fn=output.append)
        with self.assertRaisesRegex(ValueError, "尚未载入游戏"):
            app.handle("end")

    def test_m2_map_review_and_validation_commands(self) -> None:
        commands = iter(["new", "map", "review", "validate", "quit"])
        output: list[str] = []
        app = TerminalApp(
            FakeApi(), input_fn=lambda _prompt: next(commands), output_fn=output.append
        )
        self.assertEqual(0, app.run())
        text = "\n".join(output)
        self.assertIn("柳林村", text)
        self.assertIn("剧本包校验：通过", text)
        self.assertIn("结局 24/95", text)

    def test_governance_action_uses_governance_api_instead_of_legacy_quote(self) -> None:
        class GovernanceApi:
            def get_actions(self, session_id: str) -> dict:
                return {
                    "state_version": 7,
                    "actions": [{
                        "action_id": "inspect_archives",
                        "name": "查阅档案",
                        "available": True,
                        "execution_mode": "governance",
                        "target_kind": "archive",
                    }],
                }

            def get_governance(self, session_id: str) -> dict:
                return {
                    "state_version": 7,
                    "archives": [{
                        "archive_id": "archive_policy",
                        "title": "补偿安置方案",
                        "evidence_level": "E3",
                    }],
                }

            def start_governance_action(self, session_id: str, **payload) -> dict:
                self.payload = payload
                return {
                    "state_version": 8,
                    "archives": [{
                        "title": "补偿安置方案",
                        "content": "{\"key\":\"internal\",\"value\":\"不得显示\"}",
                        "private_audit": "SECRET_ROOT_AUDIT",
                        "prompt": "SECRET_PROMPT",
                        "debug_notes": {"summary": "SECRET_DEBUG"},
                        "player_sections": [{
                            "heading": "办理原则",
                            "body": "逐户合同必须以实测底账为准。",
                        }],
                    }],
                }

            def quote_action(self, *_args, **_kwargs):
                raise AssertionError("治理行动不得进入旧资源报价流程")

        api = GovernanceApi()
        output: list[str] = []
        app = TerminalApp(
            api,
            input_fn=lambda _prompt: "1",
            output_fn=output.append,
        )
        app.session_id = "game_1"
        app.state_version = 7

        app.handle("do inspect_archives")

        self.assertEqual("inspect_archives", api.payload["action_kind"])
        self.assertEqual(["archive_policy"], api.payload["archive_ids"])
        rendered = "\n".join(output)
        self.assertIn("办理原则", rendered)
        self.assertIn("逐户合同必须以实测底账为准", rendered)
        self.assertNotIn("internal", rendered)
        self.assertNotIn("不得显示", rendered)
        self.assertNotIn("SECRET_ROOT_AUDIT", rendered)
        self.assertNotIn("SECRET_PROMPT", rendered)
        self.assertNotIn("SECRET_DEBUG", rendered)

    def test_cancel_governance_recovers_an_active_action(self) -> None:
        class GovernanceApi:
            def cancel_governance_action(
                self, session_id: str, action_instance_id: str, **payload
            ) -> dict:
                self.request = {
                    "session_id": session_id,
                    "action_instance_id": action_instance_id,
                    **payload,
                }
                return {
                    "state_version": 9,
                    "action": {"status": "cancelled"},
                }

        api = GovernanceApi()
        output: list[str] = []
        app = TerminalApp(api, output_fn=output.append)
        app.session_id = "game_1"
        app.state_version = 8

        app.handle("cancel-governance govact_1")

        self.assertEqual("govact_1", api.request["action_instance_id"])
        self.assertEqual(8, api.request["state_version"])
        self.assertEqual(9, app.state_version)
        self.assertIn("已中止", "\n".join(output))

    def test_contract_audit_renders_problem_location_and_fix(self) -> None:
        output: list[str] = []
        app = TerminalApp(FakeApi(), output_fn=output.append)

        app._render_contract_audit({
            "audit_status": "reject",
            "audit_model_id": "professional-contract-auditor",
            "audit_result": {
                "summary": "存在超出授权的额外付款承诺。",
                "issues": [{
                    "category": "resource_authority",
                    "term_field": "cash_amount",
                    "text_quote": "再额外支付100万元专项补助",
                    "message": "该金额未写入结构化资源条款。",
                    "suggestion": "删除该承诺，或先取得新的批准文件。",
                }],
            },
        })

        text = "\n".join(output)
        self.assertIn("合同专业审校：不通过", text)
        self.assertIn("审校模型：professional-contract-auditor", text)
        self.assertIn("位置字段：现金补偿额", text)
        self.assertIn("位置：再额外支付100万元专项补助", text)
        self.assertIn("说明：该金额未写入结构化资源条款", text)
        self.assertIn("建议：删除该承诺，或先取得新的批准文件", text)

    def test_multiline_contract_editor_preserves_paragraphs(self) -> None:
        inputs = iter([
            "第一条：现金补偿100万元。",
            "",
            "第二条：付款日D10。",
            "完成",
        ])
        output: list[str] = []
        app = TerminalApp(
            FakeApi(),
            input_fn=lambda _prompt: next(inputs),
            output_fn=output.append,
        )

        text = app._input_multiline_contract()

        self.assertEqual(
            "第一条：现金补偿100万元。\n\n第二条：付款日D10。",
            text,
        )
        self.assertIn("可输入多行", "\n".join(output))

    def test_contract_form_error_translates_missing_fields(self) -> None:
        output: list[str] = []
        app = TerminalApp(FakeApi(), output_fn=output.append)

        app._render_contract_form_error(ApiError(
            "合同文本与结构化条款不一致",
            details={
                "missing_term_fields": [
                    "cash_amount", "payment_day",
                ],
            },
        ))

        text = "\n".join(output)
        self.assertIn("现金补偿额", text)
        self.assertIn("付款日", text)
        self.assertNotIn("cash_amount", text)

    def test_contract_form_error_translates_numeric_details(self) -> None:
        output: list[str] = []
        app = TerminalApp(FakeApi(), output_fn=output.append)

        app._render_contract_form_error(ApiError(
            "合同现金低于开局政策标准",
            code="ACTION_UNAVAILABLE",
            details={"minimum": 100, "submitted": 90},
        ))

        text = "\n".join(output)
        self.assertIn("政策最低补偿额：100", text)
        self.assertIn("本次填写金额：90", text)
        self.assertNotIn("minimum", text)
        self.assertNotIn("submitted", text)

    def test_only_editable_contract_errors_stay_in_form(self) -> None:
        self.assertTrue(TerminalApp._is_editable_contract_error(ApiError(
            "现金低于标准", code="ACTION_UNAVAILABLE"
        )))
        self.assertFalse(TerminalApp._is_editable_contract_error(ApiError(
            "账户无权操作", code="PERMISSION_DENIED"
        )))
        self.assertFalse(TerminalApp._is_editable_contract_error(ApiError(
            "状态已变化", code="STATE_VERSION_CONFLICT"
        )))
        self.assertFalse(TerminalApp._is_editable_contract_error(ApiError(
            "审校模型不可用", code="ROLE_LLM_UNAVAILABLE"
        )))
        self.assertTrue(TerminalApp._is_retryable_contract_audit_error(
            ApiError("审校模型不可用", code="ROLE_LLM_UNAVAILABLE")
        ))
        self.assertTrue(TerminalApp._is_retryable_contract_audit_error(
            ApiError("审校响应无效", code="ROLE_LLM_INVALID_RESPONSE")
        ))
        self.assertFalse(TerminalApp._is_retryable_contract_audit_error(
            ApiError("状态已变化", code="STATE_VERSION_CONFLICT")
        ))

    def test_contract_audit_retry_explains_retained_content(self) -> None:
        output: list[str] = []
        app = TerminalApp(
            FakeApi(),
            input_fn=lambda _prompt: "1",
            output_fn=output.append,
        )

        retry = app._prompt_contract_audit_retry(
            ApiError("模型暂时离线", code="ROLE_LLM_UNAVAILABLE"),
            retained_content="刚才输入的完整合同正文",
        )

        self.assertTrue(retry)
        text = "\n".join(output)
        self.assertIn("专业合同审校暂不可用", text)
        self.assertIn("刚才输入的完整合同正文仍保留", text)
        self.assertIn("本次未通过内容不会保存", text)

    def test_document_flow_shows_and_edits_text_before_countersign(self) -> None:
        class DocumentApi(FakeApi):
            def get_governance(self, _session_id):
                return {
                    "state_version": 4,
                    "documents": [{
                        "document_id": "doc_1",
                        "title": "搬迁补偿调整通知",
                        "version": 1,
                        "content": "原文件正文",
                        "status": "draft",
                        "required_countersign_ids": ["npc_finance"],
                        "countersigned_by": [],
                    }],
                }

            def edit_document(self, session_id, document_id, **payload):
                self.edit_request = (session_id, document_id, payload)
                return {
                    "state_version": 5,
                    "document": {
                        "document_id": "doc_1",
                        "title": "搬迁补偿调整通知",
                        "version": 2,
                        "content": payload["content"],
                        "status": "draft",
                        "required_countersign_ids": ["npc_finance"],
                        "countersigned_by": [],
                    },
                }

            def countersign_document(
                self, session_id, document_id, **payload
            ):
                self.countersign_request = (session_id, document_id, payload)
                return {
                    "state_version": 6,
                    "accepted": True,
                    "reason": "财政授权边界清楚。",
                    "document": {
                        "document_id": "doc_1",
                        "title": "搬迁补偿调整通知",
                        "version": 2,
                        "content": "修改后的第一条\n修改后的第二条",
                        "status": "approved",
                        "required_countersign_ids": ["npc_finance"],
                        "countersigned_by": ["npc_finance"],
                    },
                }

        inputs = iter([
            "2",
            "修改后的第一条",
            "修改后的第二条",
            "完成",
            "1",
            "0",
        ])
        output: list[str] = []
        api = DocumentApi()
        app = TerminalApp(
            api,
            input_fn=lambda _prompt: next(inputs),
            output_fn=output.append,
        )
        app.session_id = "game_1"
        app.state_version = 4

        app._process_document("doc_1")

        self.assertEqual(
            "修改后的第一条\n修改后的第二条",
            api.edit_request[2]["content"],
        )
        self.assertEqual(5, api.countersign_request[2]["state_version"])
        text = "\n".join(output)
        self.assertIn("原文件正文", text)
        self.assertIn("修改后全文", text)
        self.assertIn("此前会签已按现实程序清空", text)

    def test_service_resource_input_uses_player_facing_numbers(self) -> None:
        output: list[str] = []
        app = TerminalApp(
            FakeApi(),
            input_fn=lambda _prompt: "2=1",
            output_fn=output.append,
        )

        result = app._prompt_service_allocations([
            {
                "resource_id": "medical_review",
                "name": "血铅复检",
                "available": 3,
            },
            {
                "resource_id": "school_transfer",
                "name": "就学衔接",
                "available": 2,
            },
        ])

        self.assertEqual({"school_transfer": 1}, result)
        self.assertIn("就学衔接", "\n".join(output))

    def test_contract_field_correction_preserves_other_terms(self) -> None:
        inputs = iter(["1", "125"])
        app = TerminalApp(
            FakeApi(),
            input_fn=lambda _prompt: next(inputs),
        )
        terms = {
            "cash_amount": 100,
            "budget_envelope": "property_land",
            "housing_resource_id": None,
            "service_allocations": {},
            "payment_day": 10,
            "move_out_day": 20,
            "housing_delivery_day": 20,
            "transition_months": 12,
            "public_window_reward": True,
            "approval_document_ids": [],
            "authorization_confirmed": False,
            "real_unit_viewed": False,
            "ledger_disclosed": False,
            "old_case_resolved": False,
            "prior_payment_verified": False,
        }

        changed = app._edit_contract_term(
            terms,
            envelopes=["property_land"],
            budget_envelopes={"property_land": {"available": 1000}},
            housing=[],
            service_resources=[],
            approval_documents=[],
            resource_names={},
        )

        self.assertTrue(changed)
        self.assertEqual(125, terms["cash_amount"])
        self.assertEqual(10, terms["payment_day"])
        self.assertTrue(terms["public_window_reward"])

    def test_contract_field_exit_warns_unsaved_input_is_discarded(self) -> None:
        output: list[str] = []
        app = TerminalApp(
            FakeApi(),
            input_fn=lambda _prompt: "0",
            output_fn=output.append,
        )

        changed = app._edit_contract_term(
            {
                "cash_amount": 100,
                "budget_envelope": "property_land",
                "housing_resource_id": None,
                "service_allocations": {},
                "payment_day": 10,
                "move_out_day": 20,
                "housing_delivery_day": 20,
                "transition_months": 12,
                "public_window_reward": True,
                "approval_document_ids": [],
                "authorization_confirmed": False,
                "real_unit_viewed": False,
                "ledger_disclosed": False,
                "old_case_resolved": False,
                "prior_payment_verified": False,
            },
            envelopes=["property_land"],
            budget_envelopes={"property_land": {"available": 1000}},
            housing=[],
            service_resources=[],
            approval_documents=[],
            resource_names={},
        )

        self.assertFalse(changed)
        self.assertIn(
            "本次未通过内容不会保存",
            "\n".join(output),
        )


if __name__ == "__main__":
    unittest.main()
