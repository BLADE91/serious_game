"""仿真过程日志。"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SimulationLog:
    """规则模块之间传递的日志记录。"""

    day: int
    log_type: str
    message: str
    visible_to_player: bool = True
    related_npc_ids: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.day < 1:
            raise ValueError("SimulationLog.day must be greater than 0")
        if not self.log_type.strip():
            raise ValueError("SimulationLog.log_type must not be empty")
        if not self.message.strip():
            raise ValueError("SimulationLog.message must not be empty")
