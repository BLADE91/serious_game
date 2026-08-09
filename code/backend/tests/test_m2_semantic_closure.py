from dataclasses import replace
from pathlib import Path
import unittest

from serious_game_backend.application.action_service import ActionService
from serious_game_backend.application.ending_service import EndingAxisProjector, EndingService
from serious_game_backend.application.scripted_delta_resolver import ScriptedDeltaResolver
from serious_game_backend.application.scripted_effect_service import ScriptedEffectService
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

    def test_runtime_derived_flags_survive_hard_settlement_validation(self) -> None:
        service = ScriptedEffectService(ScriptedDeltaResolver())
        grave_session = self.session(flags={"周家祖坟事由已知"})
        service.apply(
            grave_session,
            self.package,
            self.effects(
                "dp4_04",
                "c",
                {"周家祖坟事由已知"},
                {"talk_money_count": 1},
            ),
            source_id="dp4_04:c",
        )
        self.assertIn("迁坟条件待议", grave_session.flags)

        for option_id in ("a", "b", "c"):
            with self.subTest(option_id=option_id):
                eia_session = self.session(flags={"环评已处理"})
                service.apply(
                    eia_session,
                    self.package,
                    self.effects("dp6_10", option_id, {"环评已处理"}),
                    source_id=f"dp6_10:{option_id}",
                )
                self.assertNotIn("环评已处理", eia_session.flags)

    def test_previously_shadowed_sub_endings_have_real_axis_witnesses(self) -> None:
        cases = {
            "ending_08d": (28, {"掘坟结怨"}),
            "ending_18c": (30, {"压制媒体", "掌握血铅", "据实以告"}),
            "ending_19b": (
                30,
                {"两百万已移交立案", "掩盖真相", "据实以告"},
            ),
            "ending_19c": (
                30,
                {"两百万已移交立案", "牺牲赵建国", "据实以告"},
            ),
            "ending_21d": (
                30,
                {"环评揭穿", "蒋崇岳否决", "据实以告"},
            ),
            "ending_22d": (
                32,
                {
                    "环评已处理",
                    "账目揭发",
                    "据实以告",
                    "秀英寒心",
                    "越级上访",
                    "面子优先",
                    "宗族对立",
                },
            ),
            "ending_23a": (
                30,
                {"掘坟结怨", "环评已处理", "据实以告"},
            ),
        }
        for expected_sub_ending, (signed, flags) in cases.items():
            with self.subTest(sub_ending=expected_sub_ending):
                result = EndingService(EndingAxisProjector()).finalize(
                    self.session(signed=signed, flags=flags),
                    self.package,
                )
                self.assertEqual(expected_sub_ending, result["sub_ending_id"])

    def test_all_95_sub_endings_are_structurally_reachable(self) -> None:
        base_axes = {
            "A": "压线",
            "C": "未触碰",
            "D": "干净",
            "T": "揭而已治",
            "M": "温和",
            "X": "实",
            "R": "据实以告",
            "P": "认可",
            "F": "中立",
            "Z": "中立",
            "J": "未起疑",
            "K": "离心",
            "E": "未接触",
            "V": "默许",
        }
        main_overrides = {
            "ending_01": {"D": "入局败露"},
            "ending_02": {"V": "否决", "T": "无知"},
            "ending_03": {"M": "暴力"},
            "ending_04": {"A": "溃败", "M": "施压"},
            "ending_05": {},
            "ending_06": {"A": "溃败"},
            "ending_07": {"A": "差一两户", "T": "揭而已治"},
            "ending_08": {"A": "差一两户", "T": "无知"},
            "ending_09": {"R": "串供口径"},
            "ending_10": {"X": "假"},
            "ending_11": {"R": "瞒报"},
            "ending_12": {"T": "知而捂"},
            "ending_13": {"C": "弃车保帅"},
            "ending_14": {"C": "捂住"},
            "ending_15": {"M": "施压", "P": "疏离"},
            "ending_16": {"P": "离散", "T": "无知"},
            "ending_17": {"K": "结怨"},
            "ending_18": {"E": "被压制"},
            "ending_19": {"J": "立案"},
            "ending_20": {"T": "无知"},
            "ending_21": {"T": "揭而未治"},
            "ending_22": {"A": "宽裕", "C": "端掉"},
            "ending_23": {"A": "压线"},
            "ending_24": {"D": "入局未败露"},
        }
        main_flags = {
            "ending_04": {"掘坟结怨"},
            "ending_05": {"样板充数", "居功避重"},
        }
        service = EndingService(EndingAxisProjector())
        mains = {
            item.ending_id: item for item in self.package.main_endings
        }
        reached = set()
        for sub in self.package.sub_endings:
            expected_main = mains[sub.main_ending_id]
            axes = {**base_axes, **main_overrides[sub.main_ending_id]}
            axes[expected_main.free_axis] = sub.axis_value
            flags = set(main_flags.get(sub.main_ending_id, ()))
            if expected_main.free_axis == "Z" and sub.axis_value == "掘坟结怨":
                flags.add("掘坟结怨")
                axes["M"] = "施压"
                axes["P"] = "疏离"
            selected = next(
                item
                for item in sorted(
                    self.package.main_endings,
                    key=lambda value: value.order,
                )
                if service._matches(item.condition, axes, flags)
            )
            self.assertEqual(
                expected_main.ending_id,
                selected.ending_id,
                msg=f"{sub.sub_ending_id} 被 {selected.ending_id} 遮蔽",
            )
            reached.add(sub.sub_ending_id)
        self.assertEqual(95, len(reached))

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
