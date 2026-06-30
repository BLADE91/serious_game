"""游戏全局状态。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class GameState:
    """两个规则模块共享的最小游戏状态。"""

    day: int = 1
    action_points: int = 3
    budget_remaining: int = 8000
    budget_unit: str = "万元"
    signed_households: int = 0
    total_households: int = 36
    social_stability_index: int = 70
    political_credit: int = 70
    cadre_execution_index: int = 60
    public_trust: int = 50
    env_clue: int = 0
    media_pressure: int = 30
    days_left: int = 90

    def __post_init__(self) -> None:
        if self.day < 1:
            raise ValueError("GameState.day must be greater than 0")
        if self.total_households <= 0:
            raise ValueError("GameState.total_households must be greater than 0")
        if self.days_left < 0:
            raise ValueError("GameState.days_left must not be negative")
        for name, value in (
            ("social_stability_index", self.social_stability_index),
            ("political_credit", self.political_credit),
            ("cadre_execution_index", self.cadre_execution_index),
            ("public_trust", self.public_trust),
            ("env_clue", self.env_clue),
            ("media_pressure", self.media_pressure),
        ):
            if value < 0 or value > 100:
                raise ValueError(f"GameState.{name} must be between 0 and 100")
