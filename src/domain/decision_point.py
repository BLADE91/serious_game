"""决策点与选项 —— 严肃游戏的核心互动单元。"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DecisionOption:
    """决策点中的一个可选方案。

    每个选项代表玩家可以采取的一种应对方式，
    包含成本、收益和风险。
    """

    option_id: str
    label: str              # 简短标签，如 "亲自上门劝说"
    description: str        # 选项说明，解释玩家具体要做什么
    cost_action_points: int = 1
    budget_cost: int = 0
    payoffs: dict[str, Any] = field(default_factory=dict)
    # payoffs 示例:
    # {
    #     "global": {"social_stability_index": +5, "political_credit": -3},
    #     "npc_V01": {"trust_to_player": +10},
    # }
    risks: list[str] = field(default_factory=list)
    citation: str = ""      # 支撑该选项的资料引用

    def __post_init__(self) -> None:
        if not self.option_id.strip():
            raise ValueError("DecisionOption.option_id must not be empty")
        if not self.label.strip():
            raise ValueError("DecisionOption.label must not be empty")
        if self.cost_action_points < 0:
            raise ValueError("DecisionOption.cost_action_points must not be negative")


@dataclass(frozen=True)
class DecisionPoint:
    """一个多选项决策困境。

    每个决策点代表玩家在特定情境下面临的关键选择，
    通常提供 3-5 个互斥或可组合的选项。
    """

    decision_id: str
    title: str              # 决策点标题，如 "首批入户动员策略"
    day_window: str         # 时间窗口，如 "第1-5天"
    situation: str          # 情境描述：当前面临什么困境
    options: list[DecisionOption] = field(default_factory=list)
    affected_npc_ids: list[str] = field(default_factory=list)
    trigger_condition: str = ""   # 触发条件（可为空表示必定触发）
    is_critical: bool = False     # 是否为影响结局走向的关键决策
    citations: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.decision_id.strip():
            raise ValueError("DecisionPoint.decision_id must not be empty")
        if not self.title.strip():
            raise ValueError("DecisionPoint.title must not be empty")
        if not self.day_window.strip():
            raise ValueError("DecisionPoint.day_window must not be empty")
        if not self.situation.strip():
            raise ValueError("DecisionPoint.situation must not be empty")
        if len(self.options) < 2:
            raise ValueError("DecisionPoint must have at least 2 options")
