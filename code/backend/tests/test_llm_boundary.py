from __future__ import annotations

import unittest

from serious_game_backend.application.npc_turn_service import NPCTurnService
from serious_game_backend.application.scripted_delta_resolver import ScriptedDeltaResolver
from serious_game_backend.application.state_delta_validator import StateDeltaValidator
from serious_game_backend.domain.enums import NPCStateTier
from serious_game_backend.domain.errors import RoleLLMResponseError
from serious_game_backend.domain.llm import RoleTurnContext, RoleTurnResult
from serious_game_backend.domain.npc_state import NPCState
from serious_game_backend.domain.fact_markers import (
    FACT_DISCLOSURE_MARKERS,
    fact_safety_signature,
    normalize_fact_signature,
)
from serious_game_backend.domain.story import FactDefinition


class StubRoleLLMGateway:
    def __init__(self, result: RoleTurnResult) -> None:
        self.result = result

    def run_turn(self, context: RoleTurnContext) -> RoleTurnResult:
        return self.result


class LlmBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = StateDeltaValidator(ScriptedDeltaResolver())

    def test_bands_map_to_bounded_deltas_deterministically(self) -> None:
        state = NPCState(
            npc_id="npc_test",
            state_tier=NPCStateTier.DEEP,
            trust_score=40,
            attitude_score=50,
            anxiety_score=50,
        )
        proposal = RoleTurnResult(
            npc_id="npc_test",
            dialogue="再谈谈。",
            attitude_direction="decrease",
            attitude_band="micro",
            anxiety_direction="increase",
            anxiety_band="light",
        )
        first = self.validator.validate_role_turn(
            proposal, state, random_seed="fixed-seed", source_id="opp_1"
        )
        second = self.validator.validate_role_turn(
            proposal, state, random_seed="fixed-seed", source_id="opp_1"
        )
        self.assertEqual(-5, first.attitude_delta)
        self.assertGreaterEqual(first.anxiety_delta, 5)
        self.assertLessEqual(first.anxiety_delta, 10)
        self.assertEqual(first, second)
        self.assertEqual(40, state.trust_score)

    def test_limited_npc_cannot_receive_numeric_llm_delta(self) -> None:
        state = NPCState(npc_id="npc_limited", state_tier=NPCStateTier.LIMITED)
        proposal = RoleTurnResult(
            npc_id="npc_limited",
            dialogue="按程序办。",
            attitude_direction="increase",
            attitude_band="micro",
        )
        with self.assertRaises(ValueError):
            self.validator.validate_role_turn(
                proposal, state, random_seed="fixed-seed", source_id="opp_2"
            )

    def test_npc_turn_rejects_disclosure_outside_opportunity_allowlist(self) -> None:
        result = RoleTurnResult(
            npc_id="npc_wu_xiuying",
            dialogue="我还知道另一件事。",
            disclosure_id="fact_not_allowed",
        )
        service = NPCTurnService(StubRoleLLMGateway(result), self.validator)
        context = RoleTurnContext(
            session_id="session_1",
            npc_id="npc_wu_xiuying",
            player_text="请说。",
            story_day=2,
            opportunity_id="opp_d02_wu_xiuying_first_talk",
            allowed_fact_ids=("fact_clan_power_map",),
        )

        with self.assertRaises(RoleLLMResponseError):
            service.run(
                context,
                NPCState(npc_id=context.npc_id, state_tier=NPCStateTier.LIMITED),
                random_seed="seed",
            )

    def test_npc_turn_rejects_llm_authored_flags(self) -> None:
        result = RoleTurnResult(
            npc_id="npc_wu_xiuying",
            dialogue="这件事已经办妥了。",
            flag_candidates=("flag_skip_rule_engine",),
        )
        service = NPCTurnService(StubRoleLLMGateway(result), self.validator)
        context = RoleTurnContext(
            session_id="session_1",
            npc_id="npc_wu_xiuying",
            player_text="继续。",
            story_day=2,
            opportunity_id="opp_d02_wu_xiuying_first_talk",
        )

        with self.assertRaises(RoleLLMResponseError):
            service.run(
                context,
                NPCState(npc_id=context.npc_id, state_tier=NPCStateTier.LIMITED),
                random_seed="seed",
            )

    def test_npc_turn_rejects_blank_dialogue(self) -> None:
        result = RoleTurnResult(npc_id="npc_wu_xiuying", dialogue="   ")
        service = NPCTurnService(StubRoleLLMGateway(result), self.validator)
        context = RoleTurnContext(
            session_id="session_1",
            npc_id="npc_wu_xiuying",
            player_text="继续。",
            story_day=2,
            opportunity_id="opp_d02_wu_xiuying_first_talk",
        )

        with self.assertRaises(RoleLLMResponseError):
            service.run(
                context,
                NPCState(npc_id=context.npc_id, state_tier=NPCStateTier.LIMITED),
                random_seed="seed",
            )

    def test_fact_signature_keeps_specific_phrases_without_banning_generic_words(
        self,
    ) -> None:
        signature = fact_safety_signature(FactDefinition(
            fact_id="fact_two_million_fee",
            title="两百万前期协调费",
            text="两百万前期协调费的凭证需要说明真实去处。",
            source_label="开局财政与项目卷宗，后续凭证核验",
            use_hint="可追问经手人、审批链、收款方与实际用途。",
        ))
        self.assertIn("facttwomillionfee", signature)
        self.assertIn("两百万前期协调费", signature)
        self.assertIn("凭证需要说明真实去处", signature)
        self.assertNotIn(normalize_fact_signature("凭证"), signature)
        self.assertNotIn(normalize_fact_signature("材料"), signature)

    def test_every_registered_short_alias_is_an_enforced_fact_signature(self) -> None:
        short_aliases = [
            (fact_id, alias)
            for fact_id, aliases in FACT_DISCLOSURE_MARKERS.items()
            for alias in aliases
            if len(normalize_fact_signature(alias)) < 4
        ]
        self.assertGreater(short_aliases, [])
        for fact_id, alias in short_aliases:
            with self.subTest(fact_id=fact_id, alias=alias):
                signature = fact_safety_signature(FactDefinition(
                    fact_id=fact_id,
                    title=f"{fact_id} 的权威标题",
                    text=f"{fact_id} 的权威事实正文足够具体。",
                    source_label="权威卷宗来源",
                    use_hint="仅在机会授权后向玩家披露这一具体事实。",
                ))
                normalized_alias = normalize_fact_signature(alias)
                self.assertIn(normalized_alias, signature)

                service = NPCTurnService(
                    StubRoleLLMGateway(RoleTurnResult(
                        npc_id="npc_test",
                        dialogue=f"我知道{alias}的下落。",
                    )),
                    self.validator,
                )
                with self.assertRaises(RoleLLMResponseError):
                    service.run(
                        RoleTurnContext(
                            session_id="session_1",
                            npc_id="npc_test",
                            player_text="请说。",
                            story_day=2,
                            opportunity_id="opp_test",
                            forbidden_fact_signatures={fact_id: signature},
                        ),
                        NPCState(
                            npc_id="npc_test",
                            state_tier=NPCStateTier.LIMITED,
                        ),
                        random_seed="seed",
                    )

    def test_authorized_short_usb_alias_and_disclosure_remain_allowed(self) -> None:
        result = RoleTurnResult(
            npc_id="npc_shi_wenbin",
            dialogue="我把优盘交给你，里面的材料请妥善核验。",
            disclosure_id="fact_shi_usb",
        )
        service = NPCTurnService(StubRoleLLMGateway(result), self.validator)
        validated = service.run(
            RoleTurnContext(
                session_id="session_1",
                npc_id="npc_shi_wenbin",
                player_text="你愿意交出证据吗？",
                story_day=22,
                opportunity_id="opp_22_shi_wenbin_contact",
                allowed_fact_ids=("fact_shi_usb",),
            ),
            NPCState(npc_id="npc_shi_wenbin", state_tier=NPCStateTier.LIMITED),
            random_seed="seed",
        )
        self.assertEqual("fact_shi_usb", validated.disclosure_id)
        self.assertIn("优盘", validated.dialogue)

    def test_npc_turn_rejects_malformed_result_field_types_as_domain_errors(self) -> None:
        malformed_fields = {
            "npc_id": 7,
            "dialogue": {"text": "正常对白"},
            "input_relevance": ["relevant"],
            "portrait_state": {"value": "neutral"},
            "attitude_direction": 1,
            "attitude_band": ["none"],
            "anxiety_direction": {"value": "none"},
            "anxiety_band": 0,
            "disclosure_id": ["fact_shi_usb"],
            "flag_candidates": {"flag_hidden": True},
            "will_share_with": "优盘",
            "memory_candidate": {"secret": "优盘"},
            "risk_notes": ({"secret": "优盘"},),
            "conversation_state": ["continue"],
            "exit_narrative": {"text": "优盘"},
        }
        context = RoleTurnContext(
            session_id="session_1",
            npc_id="npc_test",
            player_text="请说。",
            story_day=2,
            opportunity_id="opp_test",
        )
        state = NPCState(npc_id="npc_test", state_tier=NPCStateTier.LIMITED)
        for field_name, malformed_value in malformed_fields.items():
            with self.subTest(field_name=field_name):
                values = {
                    "npc_id": "npc_test",
                    "dialogue": "正常对白。",
                }
                values[field_name] = malformed_value
                result = RoleTurnResult(**values)
                service = NPCTurnService(StubRoleLLMGateway(result), self.validator)
                with self.assertRaises(RoleLLMResponseError):
                    service.run(context, state, random_seed="seed")


if __name__ == "__main__":
    unittest.main()
