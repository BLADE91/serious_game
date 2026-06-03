"""NPC 运行时状态。"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NPCState:
    """两个规则模块共享的最小 NPC 状态。"""

    npc_id: str
    name: str
    npc_type: str
    group: str
    trust_to_player: int
    attitude_score: int
    anxiety_level: int
    reference_point: int
    granovetter_threshold: int
    core_demand_satisfied: bool = False
    signed: bool = False
    known_info: list[str] = field(default_factory=list)
    player_promises: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.npc_id.strip():
            raise ValueError("NPCState.npc_id must not be empty")
        if not self.name.strip():
            raise ValueError("NPCState.name must not be empty")
        if self.npc_type not in {"cadre", "external", "villager"}:
            raise ValueError("NPCState.npc_type must be cadre, external, or villager")
        self._validate_score("trust_to_player", self.trust_to_player)
        self._validate_score("attitude_score", self.attitude_score)
        self._validate_score("anxiety_level", self.anxiety_level)
        self._validate_score("granovetter_threshold", self.granovetter_threshold)

    def _validate_score(self, name: str, value: int) -> None:
        if value < 0 or value > 100:
            raise ValueError(f"NPCState.{name} must be between 0 and 100")
