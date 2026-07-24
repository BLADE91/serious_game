from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from serious_game_backend.api.app import create_app
from serious_game_backend.application.action_service import ActionService
from serious_game_backend.application.ending_service import EndingAxisProjector
from serious_game_backend.application.night_simulation_service import (
    NightSimulationService,
)
from serious_game_backend.application.scripted_delta_resolver import (
    ScriptedDeltaResolver,
)
from serious_game_backend.application.scripted_effect_service import (
    ScriptedEffectService,
)
from serious_game_backend.application.trust_derivation_service import TrustDerivationService
from serious_game_backend.bootstrap import build_container
from serious_game_backend.config import Settings
from serious_game_backend.domain.enums import ActionInputMode
from serious_game_backend.domain.errors import ContentValidationError
from serious_game_backend.domain.story import ScriptedEffects
from serious_game_backend.infrastructure.repositories.codec import (
    decode_session,
    encode_session,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]


class GameplayV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        settings = Settings(
            environment="test",
            content_root=BACKEND_ROOT / "content" / "packages",
            repository="memory",
            role_llm_provider="fake",
        )
        self.container = build_container(settings)
        self.client = TestClient(create_app(settings, self.container))
        self.headers = {"X-Account-ID": "acct_gameplay_v2"}
        response = self.client.post(
            "/api/game/session",
            json={
                "client_request_id": "gameplay-v2-new-0001",
                "origin_id": "technical",
            },
            headers=self.headers,
        )
        self.assertEqual(201, response.status_code, response.text)
        self.state = response.json()
        self.session_id = self.state["session_id"]

    def action(self, payload: dict) -> dict:
        response = self.client.post(
            f"/api/game/session/{self.session_id}/action",
            json=payload,
            headers=self.headers,
        )
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def resolve_d1(self) -> dict:
        pending = self.state["pending_decision"]
        self.state = self.action({
            "input_mode": "decision",
            "client_action_id": "gameplay-v2-d1-decision",
            "state_version": self.state["state_version"],
            "decision_id": pending["decision_id"],
            "option_id": pending["option_ids"][0],
        })["visible_state"]
        return self.state

    def reach_d2_open(self) -> dict:
        self.resolve_d1()
        response = self.client.post(
            f"/api/game/session/{self.session_id}/end-day",
            json={
                "client_action_id": "gameplay-v2-d1-end",
                "state_version": self.state["state_version"],
                "active_rest": False,
            },
            headers=self.headers,
        )
        self.assertEqual(200, response.status_code, response.text)
        self.state = response.json()["visible_state"]
        pending = self.state["pending_decision"]
        self.state = self.action({
            "input_mode": "decision",
            "client_action_id": "gameplay-v2-d2-decision",
            "state_version": self.state["state_version"],
            "decision_id": pending["decision_id"],
            "option_id": pending["option_ids"][0],
        })["visible_state"]
        return self.state

    def test_resource_action_quote_and_atomic_execution(self) -> None:
        self.resolve_d1()
        quote = self.client.post(
            f"/api/game/session/{self.session_id}/actions/quote",
            json={
                "state_version": self.state["state_version"],
                "action_id": "convene_leadership_meeting",
                "target_ids": [],
                "parameters": {"topic": "搬迁进度"},
            },
            headers=self.headers,
        )
        self.assertEqual(200, quote.status_code, quote.text)
        quotation = quote.json()
        self.assertEqual(2, quotation["cost_action_points"])
        self.assertEqual(0, quotation["direct_budget_cost"])
        payload = {
            "input_mode": "resource_action",
            "client_action_id": "gameplay-v2-resource-0001",
            "state_version": quotation["state_version"],
            "action_id": "convene_leadership_meeting",
            "target_ids": [],
            "parameters": {"topic": "搬迁进度"},
            "quote_id": quotation["quote_id"],
        }
        first = self.action(payload)
        second = self.action(payload)
        self.assertEqual(first, second)
        self.assertEqual(6, first["visible_state"]["ledger"]["action_points"]["remaining"])
        self.assertEqual(7800, first["visible_state"]["ledger"]["budget"]["remaining"])
        stale = self.client.post(
            f"/api/game/session/{self.session_id}/action",
            json={**payload, "client_action_id": "gameplay-v2-resource-stale-0001"},
            headers=self.headers,
        )
        self.assertEqual(409, stale.status_code, stale.text)
        unchanged = self.client.get(
            f"/api/game/session/{self.session_id}", headers=self.headers
        ).json()
        self.assertEqual(6, unchanged["ledger"]["action_points"]["remaining"])
        self.assertEqual(7800, unchanged["ledger"]["budget"]["remaining"])
        review = self.client.get(
            f"/api/game/session/{self.session_id}/review", headers=self.headers
        ).json()
        self.assertEqual("召开班子会", review["action_timeline"][0]["name"])

    def test_v2_conversation_cannot_be_completed_by_tool_or_zero_turn_exit(self) -> None:
        self.reach_d2_open()
        bypass = self.client.post(
            f"/api/game/session/{self.session_id}/action",
            json={
                "input_mode": "tool",
                "client_action_id": "gameplay-v2-bypass-0001",
                "state_version": self.state["state_version"],
                "action_id": "home_visit",
                "opportunity_id": "opp_d02_wu_xiuying_first_talk",
            },
            headers=self.headers,
        )
        self.assertEqual(409, bypass.status_code, bypass.text)
        started = self.action({
            "input_mode": "conversation_start",
            "client_action_id": "gameplay-v2-wu-start-0001",
            "state_version": self.state["state_version"],
            "opportunity_id": "opp_d02_wu_xiuying_first_talk",
            "target_npc_id": "npc_wu_xiuying",
        })
        ended = self.action({
            "input_mode": "conversation_end",
            "client_action_id": "gameplay-v2-wu-end-0001",
            "state_version": started["state_version"],
            "conversation_id": started["conversation"]["conversation_id"],
        })
        self.assertEqual("incomplete", ended["completion_status"])
        internal = self.container.sessions.get_owned(
            self.session_id, "acct_gameplay_v2"
        )
        self.assertNotIn("flag_wu_first_talk_completed", internal.flags)
        blocked = self.client.post(
            f"/api/game/session/{self.session_id}/end-day",
            json={
                "client_action_id": "gameplay-v2-d2-blocked-end",
                "state_version": ended["state_version"],
            },
            headers=self.headers,
        )
        self.assertEqual(409, blocked.status_code, blocked.text)

    def test_overtime_requires_zero_points_and_is_once_per_day(self) -> None:
        internal = self.container.sessions.get_owned(
            self.session_id, "acct_gameplay_v2"
        )
        internal.pending_decision = None
        internal.game_state = replace(
            internal.game_state,
            action_points=0,
            points_spent_today=8,
        )
        self.container.sessions.save(internal, expected_version=internal.state_version)
        result = self.action({
            "input_mode": "overtime",
            "client_action_id": "gameplay-v2-overtime-0001",
            "state_version": internal.state_version,
            "parameters": {"points": 3},
        })
        self.assertEqual(3, result["visible_state"]["ledger"]["action_points"]["remaining"])
        duplicate = self.client.post(
            f"/api/game/session/{self.session_id}/action",
            json={
                "input_mode": "overtime",
                "client_action_id": "gameplay-v2-overtime-0002",
                "state_version": result["state_version"],
                "parameters": {"points": 1},
            },
            headers=self.headers,
        )
        self.assertEqual(409, duplicate.status_code, duplicate.text)

    def test_flag_trust_derivation_is_once_only_and_hidden(self) -> None:
        internal = self.container.sessions.get_owned(
            self.session_id, "acct_gameplay_v2"
        )
        package = self.container.packages.get("pkg_gameplay_v2")
        internal.flags.add("flag_wu_alliance")
        service = TrustDerivationService()
        service.apply(internal, package)
        first = internal.npc_states["npc_wu_xiuying"].trust_score
        service.apply(internal, package)
        self.assertEqual(70, first)
        self.assertEqual(first, internal.npc_states["npc_wu_xiuying"].trust_score)
        visible = self.client.get(
            f"/api/game/session/{self.session_id}", headers=self.headers
        )
        self.assertNotIn("trust_score", visible.text)

    def test_zhang_li_uses_all_registered_chapter_three_explicit_effects(self) -> None:
        internal = self.container.sessions.get_owned(
            self.session_id, "acct_gameplay_v2"
        )
        package = self.container.packages.get("pkg_gameplay_v2")
        service = TrustDerivationService()
        original = internal.npc_states["npc_zhang_li"]

        def derived(
            decision_id: str,
            option_id: str,
            *,
            flags: set[str] | None = None,
            story_day: int = 31,
        ) -> int:
            internal.game_state = replace(internal.game_state, story_day=story_day)
            internal.logs = [{
                "type": "decision",
                "story_day": story_day,
                "decision_id": decision_id,
                "option_id": option_id,
            }]
            internal.flags = set(flags or ())
            internal.npc_states["npc_zhang_li"] = replace(
                original,
                trust_score=40,
                trust_locked=False,
                trust_effects_applied=frozenset(),
            )
            service.apply(internal, package)
            return internal.npc_states["npc_zhang_li"].trust_score

        self.assertTrue(50 <= derived("dp3_01", "a") <= 55)
        self.assertTrue(35 <= derived("dp3_01", "b") <= 37)
        self.assertTrue(43 <= derived("dp3_08", "a") <= 45)
        self.assertTrue(
            55 <= derived("dp3_08", "a", flags={"孙强倒戈"}) <= 62
        )
        self.assertTrue(
            28
            <= derived("dp3_02", "c", flags={"自查落空"}, story_day=32)
            <= 36
        )

        sorting = package.trust_rules["explicit_decision_effects"]["npc_zhang_li"]
        sorting = {
            key: value for key, value in sorting.items()
            if key.startswith("dp3_07:")
        }
        self.assertEqual(24, len(sorting))
        for key, bounds in sorting.items():
            position = key.split(":", 1)[1].split("_").index("a")
            self.assertEqual(
                ([8, 12], [3, 5], [-8, -5], [-18, -12])[position],
                bounds,
            )

        # D45 撤离后不再接受新的显式结算。
        self.assertEqual(40, derived("dp3_10", "a", story_day=46))

    def test_d43_tea_disposition_is_a_required_auditable_scene(self) -> None:
        package = self.container.packages.get("pkg_gameplay_v2")
        day = package.story_day(43)
        self.assertIn("dp3_tea_disposition", day.decision_ids)
        decision = package.decisions["dp3_tea_disposition"]
        self.assertEqual({"a", "b", "c"}, {
            item.option_id for item in decision.options
        })
        self.assertIn("收下茶叶", decision.option("b").effects.open_flags)
        self.assertEqual(
            (8, 12),
            decision.option("c").effects.metric_deltas["cadre_discontent"],
        )
        self.assertEqual(
            [3, 5],
            package.trust_rules["explicit_decision_effects"]["npc_zhang_li"][
                "dp3_tea_disposition:c"
            ],
        )

    def test_household_registry_is_closed_and_household_actions_reject_npc_ids(self) -> None:
        package = self.container.packages.get("pkg_gameplay_v2")
        self.assertTrue(all(
            package.resource_actions[item.action_id].executor_kind == "conversation"
            for item in package.interaction_opportunities
        ))
        self.assertEqual(36, len(package.households))
        self.assertEqual(122, sum(item.registered_population for item in package.households))
        self.assertEqual(106, sum(item.actual_residents for item in package.households))
        self.assertEqual(
            4745, sum(item.legal_residential_area_m2 for item in package.households)
        )
        desk = self.client.get(
            f"/api/game/session/{self.session_id}/desk", headers=self.headers
        )
        self.assertEqual(200, desk.status_code, desk.text)
        self.assertEqual(36, len(desk.json()["household_registry"]))
        self.assertNotIn("representative_group", desk.text)
        self.assertNotIn("signing_lock_flag", desk.text)

        self.resolve_d1()
        quote = self.client.post(
            f"/api/game/session/{self.session_id}/actions/quote",
            json={
                "state_version": self.state["state_version"],
                "action_id": "party_member_demonstration",
                "target_ids": ["NING-01"],
                "parameters": {"public_matter": "政策公示"},
            },
            headers=self.headers,
        )
        self.assertEqual(200, quote.status_code, quote.text)
        invalid = self.client.post(
            f"/api/game/session/{self.session_id}/actions/quote",
            json={
                "state_version": self.state["state_version"],
                "action_id": "party_member_demonstration",
                "target_ids": ["npc_ning_dehai"],
                "parameters": {"public_matter": "政策公示"},
            },
            headers=self.headers,
        )
        self.assertEqual(409, invalid.status_code, invalid.text)

    def test_d75_night_settles_ma_before_freezing_first_batch(self) -> None:
        session = self.container.sessions.get_owned(
            self.session_id, "acct_gameplay_v2"
        )
        package = self.container.packages.get("pkg_gameplay_v2")
        session.pending_decision = None
        session.game_state = replace(
            session.game_state,
            story_day=75,
            days_left=16,
            signed_households=3,
        )
        session.flags.add("马长顺待自然触发")
        effects = ScriptedEffectService(ScriptedDeltaResolver())

        NightSimulationService(effects).run_night(session, package)

        self.assertEqual(6, session.game_state.signed_households)
        self.assertEqual(
            6, session.d75_settlement_snapshot.first_batch_signed_count
        )
        self.assertNotIn(
            "ma_changshun",
            session.d75_settlement_snapshot.pending_group_limits,
        )
        self.assertTrue(
            session.signing_batch_summary()["roster_locked"]
        )

    def test_post75_settlement_is_whitelisted_audited_and_persistent(self) -> None:
        session = self.container.sessions.get_owned(
            self.session_id, "acct_gameplay_v2"
        )
        package = self.container.packages.get("pkg_gameplay_v2")
        session.pending_decision = None
        session.game_state = replace(
            session.game_state,
            story_day=75,
            days_left=16,
            signed_households=20,
        )
        effects = ScriptedEffectService(ScriptedDeltaResolver())
        effects.freeze_d75_roster(session, package)
        session.game_state = replace(
            session.game_state,
            story_day=77,
            days_left=14,
        )
        option = package.decisions["dp6_02"].option("b")
        settlement = ActionService._effective_effects(option, session.flags)

        effects.apply(
            session,
            package,
            settlement,
            source_id="dp6_02:b",
        )
        self.assertEqual(21, session.game_state.signed_households)
        self.assertEqual(21, session.audited_signed_households())
        self.assertEqual(1, len(session.household_settlement_entries))
        entry = session.household_settlement_entries[0]
        self.assertEqual("lao_juetou", entry.household_group_id)
        self.assertEqual("post75_confirmation", entry.entry_batch)
        self.assertFalse(entry.early_reward_paid)

        restored = decode_session(encode_session(session))
        self.assertEqual(21, restored.audited_signed_households())
        self.assertEqual(entry, restored.household_settlement_entries[0])

    def test_post75_rejects_unregistered_node_and_d90_addition(self) -> None:
        session = self.container.sessions.get_owned(
            self.session_id, "acct_gameplay_v2"
        )
        package = self.container.packages.get("pkg_gameplay_v2")
        session.game_state = replace(
            session.game_state,
            story_day=75,
            days_left=16,
            signed_households=20,
        )
        effects = ScriptedEffectService(ScriptedDeltaResolver())
        effects.freeze_d75_roster(session, package)
        illegal = ScriptedEffects(
            ledger_deltas={"signed_households": (1, 1)}
        )
        session.game_state = replace(
            session.game_state,
            story_day=80,
            days_left=11,
        )
        with self.assertRaises(ContentValidationError):
            effects.apply(
                session,
                package,
                illegal,
                source_id="unregistered_late_signing",
            )
        session.game_state = replace(
            session.game_state,
            story_day=90,
            days_left=1,
        )
        with self.assertRaises(ContentValidationError):
            effects.apply(
                session,
                package,
                illegal,
                source_id="dp6_02:b",
            )

    def test_d86_zhou_recovery_requires_willing_to_wait(self) -> None:
        package = self.container.packages.get("pkg_gameplay_v2")
        branches = package.story_day(86).night_conditional_effects
        zhou_without_prepay = branches[2]

        self.assertFalse(
            zhou_without_prepay.matches(set(), {}, {"signed_households": 20})
        )
        self.assertTrue(
            zhou_without_prepay.matches(
                {"周大山肯等"}, {}, {"signed_households": 20}
            )
        )
        self.assertFalse(
            zhou_without_prepay.matches(
                {"周大山已寒心"}, {}, {"signed_households": 20}
            )
        )

    def test_d75_registers_he_only_when_a_prior_unresolved_path_exists(self) -> None:
        session = self.container.sessions.get_owned(
            self.session_id, "acct_gameplay_v2"
        )
        package = self.container.packages.get("pkg_gameplay_v2")
        session.game_state = replace(
            session.game_state,
            story_day=75,
            days_left=16,
            signed_households=20,
        )
        effects = ScriptedEffectService(ScriptedDeltaResolver())
        session.flags.add("血铅补实")
        snapshot = effects.freeze_d75_roster(session, package)
        self.assertNotIn("he_tiezhu", snapshot.pending_group_limits)

        session.d75_settlement_snapshot = None
        session.flags.add("何铁柱肯再谈")
        snapshot = effects.freeze_d75_roster(session, package)
        self.assertEqual(4, snapshot.pending_group_limits["he_tiezhu"])

    def test_d90_projection_rejects_aggregate_ledger_mismatch(self) -> None:
        session = self.container.sessions.get_owned(
            self.session_id, "acct_gameplay_v2"
        )
        package = self.container.packages.get("pkg_gameplay_v2")
        session.game_state = replace(
            session.game_state,
            story_day=75,
            days_left=16,
            signed_households=20,
        )
        ScriptedEffectService(ScriptedDeltaResolver()).freeze_d75_roster(
            session, package
        )
        session.game_state = replace(
            session.game_state,
            story_day=90,
            days_left=0,
            signed_households=21,
        )
        with self.assertRaises(ContentValidationError):
            EndingAxisProjector().project(session)


if __name__ == "__main__":
    unittest.main()
