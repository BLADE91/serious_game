from __future__ import annotations

import json
import socket
import unittest
from dataclasses import replace

from serious_game_backend.config import Settings
from serious_game_backend.domain.errors import (
    RoleLLMBudgetExceededError,
    RoleLLMResponseRetryableError,
    RoleLLMUnavailableError,
)
from serious_game_backend.domain.llm import (
    ExpressionTask,
    NightAgentContext,
    NightAgentResult,
    GovernanceLLMContext,
    SelectionOption,
    SelectionTask,
    RoleTurnContext,
)
from serious_game_backend.infrastructure.llm.openai_compatible import (
    OpenAICompatibleRoleLLMGateway,
)
from serious_game_backend.infrastructure.llm.fake import FakeRoleLLMGateway
from serious_game_backend.infrastructure.llm.player_configuration import (
    PlayerLLMConfigurationRegistry,
)
from serious_game_backend.application.night_simulation_service import (
    NightSimulationService,
)
from serious_game_backend.application.scripted_effect_service import (
    ScriptedEffectService,
)
from serious_game_backend.application.scripted_delta_resolver import (
    ScriptedDeltaResolver,
)
from serious_game_backend.infrastructure.repositories.memory import (
    InMemoryLLMCallAuditRepository,
)


class ChoiceExpressionProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            environment="test",
            role_llm_provider="openai_compatible",
            role_llm_fallback_to_fake=False,
            role_llm_max_retries=2,
        )
        self.audits = InMemoryLLMCallAuditRepository()

    @staticmethod
    def selection_task() -> SelectionTask:
        return SelectionTask(
            task_id="night-contact",
            role_id="npc_wu_xiuying",
            role_name="吴秀英",
            instruction="选择今晚是否联系干部。",
            options=(
                SelectionOption("none", "今晚不联系"),
                SelectionOption("contact_zhao", "联系赵建国"),
            ),
            selection_mode="single",
            minimum_choices=1,
            maximum_choices=1,
            session_id="session-a",
            account_id="account-a",
            operation_id="operation-a",
            story_day=10,
        )

    def test_selection_retries_only_same_real_model_and_rejects_unknown_choice(self) -> None:
        responses = iter((
            {"choice_id": "invented"},
            {"choice_id": "contact_zhao"},
        ))
        requests: list[dict] = []

        def transport(_url: str, _key: str, body: dict, _timeout: float) -> dict:
            requests.append(body)
            return {
                "choices": [{"message": {"content": json.dumps(next(responses))}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

        gateway = OpenAICompatibleRoleLLMGateway(
            self.settings, "real-key", self.audits, transport=transport
        )
        result = gateway.select(self.selection_task())

        self.assertEqual("contact_zhao", result.choice_id)
        self.assertEqual(2, len(requests))
        self.assertEqual("qwen3.6-plus", requests[0]["model"])
        self.assertIn("invented", requests[1]["messages"][-1]["content"])
        self.assertNotIn("fake", json.dumps(requests, ensure_ascii=False).casefold())

    def test_selection_exhaustion_is_retryable_and_never_repairs_illegal_business_choice(self) -> None:
        calls = 0

        def transport(_url: str, _key: str, _body: dict, _timeout: float) -> dict:
            nonlocal calls
            calls += 1
            return {
                "choices": [{"message": {"content": '{"choice_id":"outside"}'}}],
                "usage": {},
            }

        gateway = OpenAICompatibleRoleLLMGateway(
            self.settings, "real-key", self.audits, transport=transport
        )
        with self.assertRaises(RoleLLMResponseRetryableError):
            gateway.select(self.selection_task())
        self.assertEqual(3, calls)

    def test_small_protocol_reuses_validated_audit_and_enforces_call_budget(self) -> None:
        calls = 0

        def transport(*_args) -> dict:
            nonlocal calls
            calls += 1
            return {"choices": [{"message": {"content": '{"choice_id":"contact_zhao"}'}}], "usage": {}}

        gateway = OpenAICompatibleRoleLLMGateway(
            self.settings, "real-key", self.audits, transport=transport
        )
        first = gateway.select(self.selection_task())
        replay = gateway.select(self.selection_task())
        self.assertEqual(first, replay)
        self.assertEqual(1, calls)

        limited = OpenAICompatibleRoleLLMGateway(
            Settings(
                environment="test", role_llm_provider="openai_compatible",
                role_llm_fallback_to_fake=False, role_llm_max_calls_per_session=1,
            ),
            "real-key", self.audits, transport=transport,
        )
        other = replace(self.selection_task(), operation_id="operation-b")
        with self.assertRaises(RoleLLMBudgetExceededError):
            limited.select(other)

    def test_transport_failure_is_returned_without_any_fallback_gateway(self) -> None:
        gateway = OpenAICompatibleRoleLLMGateway(
            self.settings,
            "real-key",
            self.audits,
            transport=lambda *_args: (_ for _ in ()).throw(RoleLLMUnavailableError("timeout")),
        )
        with self.assertRaises(RoleLLMUnavailableError):
            gateway.select(self.selection_task())

    def test_live_reliability_gate_is_per_capability_and_fail_closed(self) -> None:
        from tools.run_choice_expression_live_matrix import validate_reliability_report

        passing = {
            "fake_calls": 0,
            "audit_providers": {"openai_compatible": 120},
            "capabilities": {
                name: {
                    "first_attempt_success_rate": 0.95,
                    "corrected_success_rate": 1.0,
                    "total": 20,
                }
                for name in (
                    "single_choice", "multiple_choice", "expression",
                    "night_followup", "contract_rendering", "document_rendering",
                )
            },
        }
        validate_reliability_report(passing)
        failing = json.loads(json.dumps(passing))
        failing["capabilities"]["night_followup"]["first_attempt_success_rate"] = 0.90
        with self.assertRaisesRegex(ValueError, "night_followup"):
            validate_reliability_report(failing)
        failing = json.loads(json.dumps(passing))
        failing["fake_calls"] = 1
        with self.assertRaisesRegex(ValueError, "Fake"):
            validate_reliability_report(failing)
        failing = json.loads(json.dumps(passing))
        failing["audit_providers"] = {
            "openai_compatible": 119,
            "fake": 1,
        }
        with self.assertRaisesRegex(ValueError, "provider"):
            validate_reliability_report(failing)

    def test_transient_transport_failure_retries_the_same_real_model(self) -> None:
        calls = 0

        def transport(_url: str, _key: str, _body: dict, _timeout: float) -> dict:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RoleLLMUnavailableError("temporary timeout")
            return {
                "choices": [{"message": {"content": '{"choice_id":"contact_zhao"}'}}],
                "usage": {},
            }

        gateway = OpenAICompatibleRoleLLMGateway(
            self.settings, "real-key", self.audits, transport=transport
        )
        result = gateway.select(self.selection_task())

        self.assertEqual("contact_zhao", result.choice_id)
        self.assertEqual(2, calls)
        audits = self.audits.list_for_session("session-a")
        self.assertEqual("openai_compatible", audits[-1].provider)
        self.assertEqual(1, audits[-1].retry_count)

    def test_expression_returns_text_only_and_retries_unsafe_stage_actions(self) -> None:
        responses = iter((
            {"text": "（她抹着眼泪跪下）县长，求求您救命。"},
            {"text": "县长，我只想知道复检什么时候能落实。"},
        ))
        requests: list[dict] = []

        def transport(_url: str, _key: str, body: dict, _timeout: float) -> dict:
            requests.append(body)
            return {
                "choices": [{"message": {"content": json.dumps(next(responses), ensure_ascii=False)}}],
                "usage": {},
            }

        gateway = OpenAICompatibleRoleLLMGateway(
            self.settings, "real-key", self.audits, transport=transport
        )
        result = gateway.express(ExpressionTask(
            task_id="npc-expression",
            role_id="npc_yuan_guilan",
            role_name="袁桂兰",
            confirmed_choice_ids=("ask_retest_date",),
            choice_summaries={"ask_retest_date": "询问儿童复检落实日期"},
            allowed_facts=("县里已经决定先垫付儿童复查费用。",),
            persona="困难户家长，说话克制直接。",
            context="县医院材料核验后的会谈。",
            session_id="session-a",
            account_id="account-a",
            operation_id="operation-expression",
            story_day=55,
        ))

        self.assertEqual("县长，我只想知道复检什么时候能落实。", result.text)
        self.assertEqual(2, len(requests))
        self.assertIn("舞台动作", requests[1]["messages"][-1]["content"])
        self.assertIn("2至4句", requests[0]["messages"][0]["content"])

    def test_expression_retries_unknown_fact_signature_without_reselecting(self) -> None:
        responses = iter((
            {"text": "那只优盘我一直留着。"},
            {"text": "我只核对已经公开的整改台账。"},
        ))
        gateway = OpenAICompatibleRoleLLMGateway(
            self.settings,
            "real-key",
            self.audits,
            transport=lambda *_args: {
                "choices": [{"message": {"content": json.dumps(next(responses), ensure_ascii=False)}}],
                "usage": {},
            },
        )
        result = gateway.express(ExpressionTask(
            task_id="safe-expression",
            role_id="npc_zhang_li",
            role_name="张立",
            confirmed_choice_ids=("review_public_ledger",),
            choice_summaries={"review_public_ledger": "只核对公开整改台账"},
            allowed_facts=("整改台账已经公开。",),
            forbidden_text_signatures=("优盘", "u盘", "fact_shi_usb"),
            persona="巡察干部",
            context="终局前复核",
        ))
        self.assertEqual("我只核对已经公开的整改台账。", result.text)

    def test_role_disclosure_selection_receives_the_players_current_question(self) -> None:
        prompts: list[str] = []

        def transport(_url: str, _key: str, body: dict, _timeout: float) -> dict:
            prompt = "\n".join(
                str(message.get("content", "")) for message in body["messages"]
            )
            prompts.append(prompt)
            if "选择角色的沟通行为" in prompt:
                content = {"choice_id": "communication_cooperative"}
            elif "选择本轮主要披露" in prompt:
                content = {
                    "choice_id": (
                        "disclose:fact_clan_power_map"
                        if "周氏宗族和散姓住户的关系" in prompt
                        else "no_disclosure"
                    )
                }
            else:
                content = {"text": "周氏宗族掌握村务话语权，散姓住户更担心程序是否公平。"}
            return {
                "choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}],
                "usage": {},
            }

        gateway = OpenAICompatibleRoleLLMGateway(
            self.settings, "real-key", self.audits, transport=transport
        )
        result = gateway.run_turn(RoleTurnContext(
            session_id="session-role-context",
            account_id="account-role-context",
            operation_id="operation-role-context",
            story_day=2,
            opportunity_id="opp_d02_wu_xiuying_first_talk",
            npc_id="npc_wu_xiuying",
            npc_name="吴秀英",
            npc_state_tier="deep",
            player_text="请明确说明周氏宗族和散姓住户的关系。",
            conversation_goal="弄清柳林村人情脉络。",
            allowed_fact_ids=("fact_clan_power_map",),
            required_disclosure_ids=("fact_clan_power_map",),
            allowed_fact_texts={"fact_clan_power_map": "周氏宗族掌握村务话语权。"},
        ))

        self.assertEqual("fact_clan_power_map", result.disclosure_id)
        self.assertTrue(any("周氏宗族和散姓住户的关系" in item for item in prompts))

    def test_governance_position_selection_receives_the_confirmed_resolution(self) -> None:
        prompts: list[str] = []

        def transport(_url: str, _key: str, body: dict, _timeout: float) -> dict:
            prompt = "\n".join(
                str(message.get("content", "")) for message in body["messages"]
            )
            is_selection = not prompts
            prompts.append(prompt)
            content = (
                {"choice_id": "approve"}
                if is_selection
                else {"text": "同意按责任清单推进。"}
            )
            return {
                "choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}],
                "usage": {},
            }

        gateway = OpenAICompatibleRoleLLMGateway(
            self.settings, "real-key", self.audits, transport=transport
        )
        result = gateway.run_governance_task(GovernanceLLMContext(
            session_id="session-governance-context",
            account_id="account-governance-context",
            operation_id="operation-governance-context",
            story_day=3,
            task="meeting_position",
            actor_id="npc_zhao_jianguo",
            actor_name="赵建国",
            actor_profile="常务副县长。",
            payload={
                "topic": "会签文件：柳林村搬迁材料专项调查通知",
                "resolution": {
                    "decision": "启动搬迁材料专项自查并公开办理节点",
                    "deadline_day": 10,
                },
            },
        ))

        self.assertEqual("approve", result.data["position"])
        self.assertIn("启动搬迁材料专项自查", prompts[0])

    def test_expression_persona_uses_public_style_instead_of_private_role_facts(self) -> None:
        prompts: list[str] = []

        def transport(_url: str, _key: str, body: dict, _timeout: float) -> dict:
            prompt = "\n".join(
                str(message.get("content", "")) for message in body["messages"]
            )
            prompts.append(prompt)
            content = (
                {"choice_id": "communication_guarded"}
                if "选择角色的沟通行为" in prompt
                else {"text": "我只按已经登记的复核流程说明。"}
            )
            return {
                "choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}],
                "usage": {},
            }

        gateway = OpenAICompatibleRoleLLMGateway(
            self.settings, "real-key", self.audits, transport=transport
        )
        gateway.run_turn(RoleTurnContext(
            session_id="session-public-persona",
            account_id="account-public-persona",
            operation_id="operation-public-persona",
            story_day=55,
            opportunity_id="opp-public-persona",
            npc_id="npc_shi_wenbin",
            npc_name="石文斌",
            npc_state_tier="deep",
            player_text="请只说明已经登记的流程。",
            role_setting="#### 石文斌：县环保站职工\n手里藏着一只优盘。",
            big_five={
                "openness": 55,
                "conscientiousness": 80,
                "extraversion": 30,
                "agreeableness": 50,
                "neuroticism": 60,
            },
            conversation_goal="核对公开流程。",
        ))

        expression_prompt = prompts[-1]
        self.assertNotIn("优盘", expression_prompt)
        self.assertIn("说话简短克制", expression_prompt)

    def test_personal_configuration_requires_all_six_real_capability_probes(self) -> None:
        calls: list[str] = []

        def transport(_url: str, _key: str, body: dict, _timeout: float) -> dict:
            prompt = "\n".join(
                str(message.get("content", "")) for message in body["messages"]
            )
            calls.append(prompt)
            if '只返回 JSON：{"choice_ids"' in prompt:
                content = {"choice_ids": ["option_a", "option_b"]}
            elif '只返回 JSON：{"choice_id"' in prompt:
                content = {"choice_id": "option_a"}
            else:
                content = {"text": "已按确认事项形成简短、明确的公开表述。"}
            return {
                "choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}],
                "usage": {},
            }

        registry = PlayerLLMConfigurationRegistry(
            self.settings,
            self.audits,
            FakeRoleLLMGateway(),
            transport=transport,
            resolver=lambda host, port, **_kwargs: [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))
            ],
        )
        status = registry.use_personal(
            "scope-a",
            base_url="https://model.example/v1",
            api_key="private-key",
            model="player-model",
        )

        self.assertEqual("compatible", status.compatibility_status)
        self.assertIsNotNone(status.tested_at)
        self.assertEqual(
            {
                "single_choice": "passed",
                "multiple_choice": "passed",
                "expression": "passed",
                "night_followup": "passed",
                "contract_rendering": "passed",
                "document_rendering": "passed",
            },
            status.capabilities,
        )
        self.assertEqual(6, len(calls))
        self.assertNotIn("private-key", json.dumps(status.public_dict(), ensure_ascii=False))

    def test_night_phases_use_tiny_choice_or_expression_payloads(self) -> None:
        requests: list[str] = []

        def transport(_url: str, _key: str, body: dict, _timeout: float) -> dict:
            prompt = "\n".join(
                str(message.get("content", "")) for message in body["messages"]
            )
            requests.append(prompt)
            if "你只负责把已经确认的业务选择写成自然语言" in prompt:
                content = {"text": "我今晚只核对已经公开的整改进度。"}
            elif '只返回 JSON：{"choice_ids"' in prompt:
                content = {"choice_ids": ["npc_zhao_jianguo"]}
            elif "night_followup_plan_a" in prompt:
                content = {"choice_id": "night_followup_plan_a"}
            else:
                content = {"choice_id": "night_hold_position"}
            return {
                "choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}],
                "usage": {},
            }

        gateway = OpenAICompatibleRoleLLMGateway(
            self.settings, "real-key", self.audits, transport=transport
        )
        common = dict(
            session_id="session-night",
            account_id="account-night",
            story_day=84,
            scene_id="night_d84_inspection_followup",
            npc_id="npc_zhang_li",
            npc_name="张立",
            role_setting="巡察干部，只依据已登记整改发言。",
            big_five={},
            counterpart_ids=("npc_zhao_jianguo",),
            scene_goal="核对已登记整改进度。",
        )
        contact = gateway.run_night_turn(NightAgentContext(
            operation_id="night-contact",
            phase="contact_selection",
            max_contacts=1,
            minimum_contacts=1,
            **common,
        ))
        dialogue = gateway.run_night_turn(NightAgentContext(
            operation_id="night-dialogue",
            phase="dialogue",
            transcript=({"speaker": "npc_zhao_jianguo", "text": "先核对台账。"},),
            **common,
        ))
        action = gateway.run_night_turn(NightAgentContext(
            operation_id="night-action",
            phase="action_selection",
            allowed_actions=({
                "action_id": "night_hold_position",
                "name": "保留意见",
                "description": "不改变权威状态。",
                "allowed_target_ids": [],
                "allowed_topics": [],
            },),
            **common,
        ))
        followup = gateway.run_night_turn(NightAgentContext(
            operation_id="night-followup",
            phase="followup_initiation",
            allowed_followup_type="cadre_meeting",
            allowed_followup_plans=({
                "plan_id": "night_followup_plan_a",
                "followup_type": "cadre_meeting",
                "participant_ids": ["npc_zhang_li", "npc_zhao_jianguo"],
                "agenda": "核对逾期整改责任和完成期限。",
                "demands": ["逐项确认责任人与期限"],
                "urgency": "high",
            },),
            **common,
        ))

        self.assertEqual(("npc_zhao_jianguo",), contact.contact_ids)
        self.assertEqual("我今晚只核对已经公开的整改进度。", dialogue.dialogue)
        self.assertEqual("night_hold_position", action.action_id)
        self.assertTrue(followup.initiate_followup)
        self.assertEqual("night_followup_plan_a", followup.rationale)
        self.assertEqual(4, len(requests))
        self.assertTrue(all("participant_ids" not in item or "合法候选" in item for item in requests))
        self.assertIn("本场景必须至少联系1人", requests[0])
        self.assertNotIn("没有必要时返回空数组", requests[0])

    def test_technical_night_failure_aborts_instead_of_settling_hold_position(self) -> None:
        service = NightSimulationService(
            ScriptedEffectService(ScriptedDeltaResolver()),
            night_llm=FakeRoleLLMGateway(night_fixture="malformed"),
        )
        failures: list[dict] = []
        context = NightAgentContext(
            session_id="session-night",
            account_id="account-night",
            operation_id="night-failure",
            story_day=10,
            scene_id="night_d10_county_reporting",
            phase="contact_selection",
            npc_id="npc_sun_qiang",
            npc_name="孙强",
            role_setting="镇干部",
            big_five={},
            counterpart_ids=("npc_zhao_jianguo",),
            max_contacts=1,
        )

        with self.assertRaises(RoleLLMResponseRetryableError):
            service._safe_night_turn(
                context,
                failures,
                forbidden_signatures={},
            )

        self.assertEqual("ROLE_LLM_INVALID_RESPONSE", failures[0]["error_code"])
        self.assertNotIn("night_hold_position", json.dumps(failures))

    def test_night_expression_receives_complete_unknown_fact_boundary_before_generation(self) -> None:
        captured: list[NightAgentContext] = []

        class CapturingGateway:
            def run_night_turn(_self, context: NightAgentContext) -> NightAgentResult:
                captured.append(context)
                return NightAgentResult(
                    npc_id=context.npc_id,
                    model_id="real-model",
                    dialogue="只讨论已经公开的复检安排。",
                )

        service = NightSimulationService(
            ScriptedEffectService(ScriptedDeltaResolver()),
            night_llm=CapturingGateway(),
        )
        context = NightAgentContext(
            session_id="session-night-boundary",
            account_id="account-night-boundary",
            operation_id="operation-night-boundary",
            story_day=55,
            scene_id="night_d55_environment_evidence",
            phase="dialogue",
            npc_id="npc_shi_wenbin",
            npc_name="石文斌",
            role_setting="环保局干部",
            big_five={},
            counterpart_ids=("npc_ke_qinian",),
        )
        result = service._safe_night_turn(
            context,
            [],
            forbidden_signatures={
                "fact_secret": ("fact_secret", "未公开证据原文")
            },
        )

        self.assertIsNotNone(result)
        self.assertEqual(1, len(captured))
        self.assertIn("fact_secret", captured[0].forbidden_disclosure_markers)
        self.assertIn("未公开证据原文", captured[0].forbidden_disclosure_markers)

    def test_role_turn_uses_behavior_and_disclosure_choices_before_expression(self) -> None:
        calls: list[str] = []

        def transport(_url: str, _key: str, body: dict, _timeout: float) -> dict:
            prompt = "\n".join(
                str(message.get("content", "")) for message in body["messages"]
            )
            calls.append(prompt)
            if "你只负责把已经确认的业务选择写成自然语言" in prompt:
                content = {"text": "周家在村里说话分量重，安置口径得先讲明白。"}
            elif "communication_cooperative" in prompt:
                content = {"choice_id": "communication_cooperative"}
            elif "disclose:fact_clan_map" in prompt:
                content = {"choice_id": "disclose:fact_clan_map"}
            else:
                self.fail("unexpected protocol prompt")
            return {
                "choices": [{"message": {"content": json.dumps(content, ensure_ascii=False)}}],
                "usage": {},
            }

        gateway = OpenAICompatibleRoleLLMGateway(
            self.settings, "real-key", self.audits, transport=transport
        )
        result = gateway.run_turn(RoleTurnContext(
            session_id="session-role",
            account_id="account-role",
            operation_id="role-turn",
            story_day=2,
            npc_id="npc_wu_xiuying",
            npc_name="吴秀英",
            player_text="村里现在最担心什么？",
            opportunity_id="opp-wu",
            role_setting="村民代表，说话直接克制。",
            allowed_fact_ids=("fact_clan_map",),
            allowed_fact_texts={"fact_clan_map": "周氏宗族在村内有公开影响力。"},
            conversation_goal="了解公开的村庄关系。",
        ))

        self.assertEqual("fact_clan_map", result.disclosure_id)
        self.assertEqual("increase", result.attitude_direction)
        self.assertEqual("micro", result.attitude_band)
        self.assertEqual("周家在村里说话分量重，安置口径得先讲明白。", result.dialogue)
        self.assertEqual(3, len(calls))

    def test_contract_and_document_business_structures_are_engine_owned(self) -> None:
        prompts: list[str] = []

        def transport(_url: str, _key: str, body: dict, _timeout: float) -> dict:
            prompt = "\n".join(
                str(message.get("content", "")) for message in body["messages"]
            )
            prompts.append(prompt)
            return {
                "choices": [{"message": {"content": json.dumps({
                    "text": "双方依照已经确认的责任和期限办理，不另作口头承诺。"
                }, ensure_ascii=False)}}],
                "usage": {},
            }

        gateway = OpenAICompatibleRoleLLMGateway(
            self.settings, "real-key", self.audits, transport=transport
        )
        contract = gateway.run_governance_task(GovernanceLLMContext(
            session_id="session-gov",
            account_id="account-gov",
            operation_id="contract-draft",
            story_day=55,
            task="draft_contract",
            actor_id="contract_writer",
            actor_name="合同文书模型",
            actor_profile="只转写已确认条款。",
            payload={
                "contract_id": "contract-001",
                "household_id": "WU-01",
                "signatory_name": "吴秀英",
                "term_sheet": {
                    "policy_document_id": "doc-policy-01",
                    "cash_amount": 45,
                    "budget_envelope": "property_land",
                    "housing_resource_id": "housing_d1_80",
                    "service_allocations": {"medical_retest": 1},
                    "payment_day": 56,
                    "move_out_day": 65,
                    "housing_delivery_day": 66,
                },
            },
        ))
        document = gateway.run_governance_task(GovernanceLLMContext(
            session_id="session-gov",
            account_id="account-gov",
            operation_id="document-draft",
            story_day=2,
            task="draft_document",
            actor_id="document_writer",
            actor_name="行政文书模型",
            actor_profile="只转写会议决议。",
            payload={
                "meeting_id": "meeting-001",
                "document_type": "专项调查通知",
                "title": "柳林村搬迁专项调查通知",
                "resolution": {
                    "decision": "开展专项调查",
                    "target_scope": "柳林村36户",
                    "responsible_ids": ["npc_zhao_jianguo"],
                    "deadline_day": 10,
                    "public_scope": ["专班"],
                    "resource_authorization_limits": {"risk_reserve": 10},
                },
            },
        ))

        self.assertIn("contract-001", contract.data["contract_text"])
        self.assertIn("45万元", contract.data["contract_text"])
        self.assertEqual(
            "doc-policy-01", contract.data["term_references"]["policy_document_id"]
        )
        self.assertIn("柳林村36户", document.data["document_text"])
        self.assertIn("D10", document.data["document_text"])
        self.assertEqual(2, len(prompts))
        self.assertTrue(all("issues" not in prompt for prompt in prompts))


if __name__ == "__main__":
    unittest.main()
