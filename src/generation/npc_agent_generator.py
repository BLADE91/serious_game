"""NPC Agent 生成器接口。"""

from abc import ABC, abstractmethod

from src.domain.agent_generation import AgentGenerationRequest, AgentGenerationResult


class NpcAgentGenerator(ABC):
    """生成结构化 NPC Agent 配置。"""

    @abstractmethod
    def generate(self, request: AgentGenerationRequest) -> AgentGenerationResult:
        """根据 query 和可选资料上下文生成 NPC Agent。"""
