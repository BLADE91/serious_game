from dataclasses import replace
from pathlib import Path
import unittest

from serious_game_backend.application.action_service import ActionService
from serious_game_backend.application.ending_service import EndingAxisProjector, EndingService
from serious_game_backend.domain.game_session import GameSession
from serious_game_backend.domain.game_state import GameState
from serious_game_backend.domain.conversation import ActiveConversation
from serious_game_backend.infrastructure.repositories.codec import decode_session, encode_session
from serious_game_backend.infrastructure.script_packages.file_loader import FileScriptPackageLoader


PACKAGE_DIR = Path(__file__).parents[1] / "content" / "packages" / "pkg_backend_dev_v1"


class M2SemanticClosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.package = FileScriptPackageLoader().load(PACKAGE_DIR)

    def session(self, *, signed: int = 0, flags: set[str] | None = None) -> GameSession:
        return GameSession(
            session_id="semantic-session",
            account_id="account",
            package_id=self.package.package_id,
            package_version=self.package.package_version,
            package_content_hash=self.package.content_hash,
            random_seed="semantic-seed",
            game_state=replace(GameState.new_game(), signed_households=signed),
            origin_id="technical",
            flags=set(flags or ()),
        )

    def effects(self, decision_id: str, option_id: str, flags=(), context=None):
        option = self.package.decisions[decision_id].option(option_id)
        return ActionService._effective_effects(
            option,
            set(flags),
            {},
            {"budget_remaining": 8000, "chapter_overtime_count": 0},
            context or {},
            decision_id=decision_id,
        )

    def test_dp5_10_opens_exactly_one_attitude(self) -> None:
        both = self.effects("dp5_10", "a", {"掌握血铅", "旧账缺口已坐实"})
        one = self.effects("dp5_10", "a", {"掌握血铅"})
        none = self.effects("dp5_10", "a")
        self.assertEqual({"蒋崇岳知情", "蒋崇岳背书"}, set(both.open_flags))
        self.assertEqual({"蒋崇岳知情", "蒋崇岳默许"}, set(one.open_flags))
        self.assertEqual({"蒋崇岳知情", "蒋崇岳弃保"}, set(none.open_flags))

    def test_dp6_10_separates_veto_abandon_and_maintained_endorsement(self) -> None:
        veto = self.effects("dp6_10", "a", {"账目揭发"})
        abandon = self.effects("dp6_10", "c", {"蒋崇岳背书", "环评已处理"})
        maintained = self.effects("dp6_10", "b", {"蒋崇岳背书", "环评已处理"})
        self.assertIn("蒋崇岳否决", veto.open_flags)
        self.assertIn("蒋崇岳弃保", abandon.open_flags)
        self.assertNotIn("蒋崇岳否决", abandon.open_flags)
        self.assertFalse({"蒋崇岳否决", "蒋崇岳弃保"} & maintained.open_flags)

    def test_dp4_04_uses_persisted_node_context(self) -> None:
        success = self.effects("dp4_04", "c", {"周家祖坟事由已知"}, {"talk_money_count": 0})
        fallback = self.effects("dp4_04", "c", {"周家祖坟事由已知"}, {"talk_money_count": 1})
        self.assertIn("迁坟条件被接受", success.open_flags)
        self.assertEqual({"迁坟条件待议"}, set(fallback.open_flags))
        self.assertFalse(fallback.ledger_deltas)

        session = self.session()
        from serious_game_backend.domain.events import PendingDecision
        session.pending_decision = PendingDecision(
            event_instance_id="event",
            decision_id="dp4_04",
            option_ids=("b", "c"),
            context={"talk_money_count": 2, "listened_once": True},
        )
        restored = decode_session(encode_session(session))
        self.assertEqual(session.pending_decision.context, restored.pending_decision.context)

        session.active_conversation = ActiveConversation(
            conversation_id="conv_persisted",
            opportunity_id="opp_d02_wu_xiuying_first_talk",
            npc_id="npc_wu_xiuying",
            story_day=2,
            turn_count=1,
            transcript=[
                {"speaker": "player", "text": "我想先听真话。"},
                {"speaker": "npc", "text": "那就先把账摊开。"},
            ],
        )
        restored = decode_session(encode_session(session))
        self.assertEqual("conv_persisted", restored.active_conversation.conversation_id)
        self.assertEqual(1, restored.active_conversation.turn_count)
        self.assertEqual(session.active_conversation.transcript,
                         restored.active_conversation.transcript)

    def test_option_guards_and_early_routes_follow_script(self) -> None:
        medical = self.package.decisions["dp4_08"].option("a")
        cleanup = self.package.decisions["ev4_04"].option("b")
        self.assertFalse(medical.is_available(set(), {}, {"budget_remaining": 21}))
        self.assertTrue(medical.is_available(set(), {}, {"budget_remaining": 22}))
        self.assertFalse(cleanup.is_available({"围堰漫溢未处置"}, {}, {}))
        self.assertEqual(19, self.package.decisions["dp2_03"].early_day)
        self.assertEqual(21, self.package.decisions["dp2_04"].early_day)

    def test_delayed_choice_and_paid_recoveries_are_registered(self) -> None:
        followup = self.package.decisions["ev3_01_followup"]
        self.assertEqual(("a", "b", "c"), tuple(item.option_id for item in followup.options))
        self.assertEqual(frozenset({"上交矛盾"}), followup.required_flags)
        by_id = {item.opportunity_id: item for item in self.package.interaction_opportunities}
        self.assertEqual(32, len(by_id))
        self.assertTrue(all(item.opening_narrative for item in by_id.values()))
        self.assertTrue(all(item.conversation_goal for item in by_id.values()))
        self.assertEqual(
            {"opp_d53_tan_laoliu_paid_recovery", "opp_d55_yuan_guilan_paid_recovery", "opp_d69_zhou_mancang_restart"},
            set(by_id) & {
                "opp_d53_tan_laoliu_paid_recovery",
                "opp_d55_yuan_guilan_paid_recovery",
                "opp_d69_zhou_mancang_restart",
            },
        )
        self.assertEqual("dp5_04_recovery", by_id["opp_d69_zhou_mancang_restart"].completion_decision_id)

    def test_d90_guard_and_full_band_copy_use_actual_household_count(self) -> None:
        session = self.session(signed=30)
        result = EndingService(EndingAxisProjector()).finalize(session, self.package)
        self.assertIn("最后一公里攻坚成功", session.flags)
        self.assertEqual("压线", result["axes"]["A"])
        rendered = EndingService._render_sub_text("三十六户全签了，一户不差。", 34)
        self.assertIn("34/36 户", rendered)
        self.assertNotIn("一户不差", rendered)


if __name__ == "__main__":
    unittest.main()
