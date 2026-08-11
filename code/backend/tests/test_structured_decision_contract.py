from serious_game_backend.application.visible_state import STRUCTURED_DECISION_LABELS


def test_four_fronts_sorting_decision_exposes_player_facing_labels():
    labels = STRUCTURED_DECISION_LABELS["dp2_08"]
    assert set(labels) == {"a", "b", "c", "d"}
    assert all("线" in labels[item] for item in labels)
