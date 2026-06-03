"""游戏行动规则与执行结果。"""

from dataclasses import dataclass, field
from typing import Any

from src.domain.simulation_log import SimulationLog


@dataclass(frozen=True)
class GameActionRule:
    """剧本生成器输出、规则模块消费的行动规则。"""

    action_id: str
    name: str
    cost_action_points: int
    budget_cost: int = 0
    allowed_targets: list[str] = field(default_factory=list)
    preconditions: list[str] = field(default_factory=list)
    forbidden_conditions: list[str] = field(default_factory=list)
    direct_payoff: dict[str, Any] = field(default_factory=dict)
    side_effects: list[str] = field(default_factory=list)
    risk_notes: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.action_id.strip():
            raise ValueError("GameActionRule.action_id must not be empty")
        if not self.name.strip():
            raise ValueError("GameActionRule.name must not be empty")
        if self.cost_action_points < 0:
            raise ValueError("GameActionRule.cost_action_points must not be negative")


@dataclass(frozen=True)
class ActionResult:
    """白天行动模块的标准输出。"""

    action_name: str
    cost_action_points: int
    budget_delta: int = 0
    game_state_changes: dict[str, Any] = field(default_factory=dict)
    npc_state_changes: dict[str, dict[str, Any]] = field(default_factory=dict)
    risk_notes: list[str] = field(default_factory=list)
    logs: list[SimulationLog] = field(default_factory=list)
