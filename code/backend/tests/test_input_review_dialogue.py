from types import SimpleNamespace

from serious_game_backend.application.input_review_service import InputReviewService


class FailingGateway:
    def run_governance_task(self, _context):
        raise AssertionError("normal greetings must not be rejected by the model gate")


def session():
    return SimpleNamespace(
        session_id="session-greeting",
        account_id="account-greeting",
        game_state=SimpleNamespace(story_day=1),
    )


def test_normal_greeting_is_valid_inside_an_active_npc_scene():
    relevant, reason = InputReviewService(FailingGateway()).review(
        session(),
        operation_id="greeting-1",
        player_text="周大山，您好！",
        scene_goal="了解搬迁诉求",
    )
    assert relevant is True
    assert "正常问候" in reason


def test_punctuation_only_is_rejected_without_calling_model():
    relevant, reason = InputReviewService(FailingGateway()).review(
        session(),
        operation_id="punctuation-1",
        player_text="、……",
        scene_goal="了解搬迁诉求",
    )
    assert relevant is False
    assert "没有包含" in reason
