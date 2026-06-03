"""NPC Agent 生成请求与结果模型。"""

from dataclasses import dataclass, field

from src.domain.npc_agent import NpcAgentConfig
from src.domain.source_context import SourceContext


@dataclass(frozen=True)
class AgentGenerationRequest:
    """生成结构化 NPC Agent 的输入。"""

    query: str
    contexts: list[SourceContext] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("AgentGenerationRequest.query must not be empty")


@dataclass(frozen=True)
class AgentGenerationResult:
    """生成后的 Agent 以及生成过程元数据。"""

    agent: NpcAgentConfig
    contexts_used: list[SourceContext] = field(default_factory=list)
    generation_notes: list[str] = field(default_factory=list)
