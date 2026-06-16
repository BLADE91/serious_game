"""多结局条件 —— 根据玩家表现触发不同结局。"""

from dataclasses import dataclass, field


ENDING_TYPES = ("good", "neutral", "bad")


@dataclass(frozen=True)
class EndingCondition:
    """一个结局及其触发条件。"""

    ending_id: str
    title: str              # 如 "完美收官" / "妥协推进" / "问责离场"
    description: str        # 结局叙述
    conditions: list[str] = field(default_factory=list)
    # 触发条件示例: ["signed_households >= 34", "social_stability_index >= 60"]
    ending_type: str = "neutral"  # good / neutral / bad

    def __post_init__(self) -> None:
        if not self.ending_id.strip():
            raise ValueError("EndingCondition.ending_id must not be empty")
        if not self.title.strip():
            raise ValueError("EndingCondition.title must not be empty")
        if self.ending_type not in ENDING_TYPES:
            raise ValueError(
                f"EndingCondition.ending_type must be one of {ENDING_TYPES}"
            )
