from __future__ import annotations

from pathlib import Path
import unittest

from serious_game_backend.application.ending_service import EndingAxisProjector, EndingService
from serious_game_backend.domain.events import PendingDecision
from serious_game_backend.domain.game_session import GameSession
from serious_game_backend.domain.game_state import GameState
from serious_game_backend.infrastructure.repositories.codec import decode_session, encode_session
from serious_game_backend.infrastructure.script_packages.file_loader import FileScriptPackageLoader


PACKAGE_DIR = Path(__file__).parents[1] / "content" / "packages" / "pkg_gameplay_v3"


class PlayRound6RegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.package = FileScriptPackageLoader().load(PACKAGE_DIR)

    def session(self) -> GameSession:
        return GameSession(
            session_id="round6-session",
            account_id="round6-account",
            package_id=self.package.package_id,
            package_version=self.package.package_version,
            package_content_hash=self.package.content_hash,
            random_seed="round6-seed",
            game_state=GameState.new_game(),
            origin_id="technical",
        )

    def test_pending_decision_scene_roundtrips_and_old_snapshot_is_safe(self) -> None:
        session = self.session()
        session.pending_decision = PendingDecision(
            event_instance_id="event-round6",
            decision_id="dp5_09",
            option_ids=("a",),
            visible_title="场景三·祠堂那块地",
            visible_text="现在必须决定。",
            scene_id="C05_S02",
        )
        encoded = encode_session(session)
        self.assertEqual("C05_S02", encoded["pending_decision"]["scene_id"])
        self.assertEqual("C05_S02", decode_session(encoded).pending_decision.scene_id)

        del encoded["pending_decision"]["scene_id"]
        self.assertIsNone(decode_session(encoded).pending_decision.scene_id)

    def test_ending_feed_does_not_repeat_main_title_and_ending_06_is_complete(self) -> None:
        session = self.session()
        result = EndingService(EndingAxisProjector()).finalize(session, self.package)
        feed = session.narrative_feed[-1].text
        self.assertFalse(feed.startswith(f"结局：{result['main_ending_name']}"))
        self.assertTrue(feed.startswith(f"余波：{result['sub_ending_title']}"))
        ending_06 = next(item for item in self.package.main_endings if item.ending_id == "ending_06")
        self.assertIn("没有登门", ending_06.text)
        self.assertNotIn("没有登。", ending_06.text)


if __name__ == "__main__":
    unittest.main()
