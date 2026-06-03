"""结构化 NPC Agent 配置。"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class NpcAgentConfig:
    """只包含数据的 NPC Agent 产物，便于安全存储和加载。"""

    id: str
    name: str
    role: str
    background: str
    goals: list[str] = field(default_factory=list)
    personality_traits: list[str] = field(default_factory=list)
    knowledge_summary: str = ""
    dialogue_style: str = ""
    behavior_rules: list[str] = field(default_factory=list)
    source_context_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("NpcAgentConfig.id must not be empty")
        if not self.name.strip():
            raise ValueError("NpcAgentConfig.name must not be empty")
        if not self.role.strip():
            raise ValueError("NpcAgentConfig.role must not be empty")
