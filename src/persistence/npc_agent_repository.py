"""用于存储生成后 NPC Agent 的仓库接口。"""

from abc import ABC, abstractmethod

from src.domain.npc_agent import NpcAgentConfig


class NpcAgentRepository(ABC):
    """存储生成后的 NPC Agent 配置。"""

    @abstractmethod
    def save(self, agent: NpcAgentConfig) -> NpcAgentConfig:
        """持久化 NPC Agent 配置。"""

    @abstractmethod
    def get_by_id(self, agent_id: str) -> NpcAgentConfig | None:
        """按 id 返回 NPC Agent；不存在时返回 None。"""

    @abstractmethod
    def list_all(self) -> list[NpcAgentConfig]:
        """返回所有已存储的 NPC Agent 配置。"""


class InMemoryNpcAgentRepository(NpcAgentRepository):
    """数据库持久化接入前使用的内存仓库。"""

    def __init__(self) -> None:
        self._agents: dict[str, NpcAgentConfig] = {}

    def save(self, agent: NpcAgentConfig) -> NpcAgentConfig:
        self._agents[agent.id] = agent
        return agent

    def get_by_id(self, agent_id: str) -> NpcAgentConfig | None:
        if not agent_id.strip():
            raise ValueError("agent_id must not be empty")
        return self._agents.get(agent_id)

    def list_all(self) -> list[NpcAgentConfig]:
        return list(self._agents.values())
