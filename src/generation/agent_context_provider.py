"""面向未来数据库或 RAG 检索的上下文提供接口。"""

from abc import ABC, abstractmethod

from src.domain.source_context import SourceContext


class AgentContextProvider(ABC):
    """根据 Agent 生成 query 获取资料上下文。"""

    @abstractmethod
    def find_contexts(self, query: str) -> list[SourceContext]:
        """返回与 query 相关的资料上下文。"""


class EmptyAgentContextProvider(AgentContextProvider):
    """数据库检索实现完成前使用的空上下文提供器。"""

    def find_contexts(self, query: str) -> list[SourceContext]:
        if not query.strip():
            raise ValueError("query must not be empty")
        return []
