"""三幕结构 —— 控制 45 分钟严肃游戏的叙事节奏。"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ActStructure:
    """游戏的一幕，包含目标和归属的决策点 ID 列表。"""

    act_number: int             # 1 / 2 / 3
    title: str                  # 如 "开局破冰" / "矛盾升级" / "收束结局"
    day_range: str              # 如 "第1-15天"
    goal: str                   # 本幕目标，玩家需要达成的阶段性任务
    description: str            # 本幕概要，叙述当前阶段的形势
    decision_point_ids: list[str] = field(default_factory=list)
    estimated_duration_minutes: int = 15

    def __post_init__(self) -> None:
        if self.act_number not in {1, 2, 3}:
            raise ValueError("ActStructure.act_number must be 1, 2, or 3")
        if not self.title.strip():
            raise ValueError("ActStructure.title must not be empty")
        if not self.day_range.strip():
            raise ValueError("ActStructure.day_range must not be empty")
        if not self.goal.strip():
            raise ValueError("ActStructure.goal must not be empty")
