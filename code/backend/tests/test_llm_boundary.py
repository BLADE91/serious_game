from __future__ import annotations

import unittest

from serious_game_backend.application.npc_turn_service import NPCTurnService
from serious_game_backend.application.scripted_delta_resolver import ScriptedDeltaResolver
from serious_game_backend.application.state_delta_validator import StateDeltaValidator
from serious_game_backend.domain.enums import NPCStateTier
from serious_game_backend.domain.errors import RoleLLMResponseError
from serious_game_backend.domain.llm import RoleTurnContext, RoleTurnResult
from serious_game_backend.domain.npc_state import NPCState


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


if __name__ == "__main__":
    unittest.main()
