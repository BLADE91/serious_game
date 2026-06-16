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

    def __post_init__(self) -> None:
        if self.day < 1:
            raise ValueError("GameState.day must be greater than 0")
        if self.total_households <= 0:
            raise ValueError("GameState.total_households must be greater than 0")
