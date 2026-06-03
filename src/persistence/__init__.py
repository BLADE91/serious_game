"""严肃游戏后端状态的持久化适配器。"""

from src.persistence.npc_agent_repository import InMemoryNpcAgentRepository, NpcAgentRepository

__all__ = [
    "InMemoryNpcAgentRepository",
    "NpcAgentRepository",
]
