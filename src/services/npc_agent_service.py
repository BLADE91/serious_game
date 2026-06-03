"""用于生成和读取 NPC Agent 的工作流服务。"""

from src.domain.agent_generation import AgentGenerationRequest, AgentGenerationResult
from src.domain.npc_agent import NpcAgentConfig
from src.domain.source_context import SourceContext
from src.generation.agent_context_provider import AgentContextProvider, EmptyAgentContextProvider
from src.generation.iterative_agent_context_provider import IterativeAgentContextProvider
from src.generation.npc_agent_generator import NpcAgentGenerator
from src.generation.qwen_npc_agent_generator import QwenNpcAgentGenerator
from src.persistence.npc_agent_repository import InMemoryNpcAgentRepository, NpcAgentRepository


class NpcAgentService:
    """编排上下文获取、Agent 生成和持久化。"""

    def __init__(
        self,
        context_provider: AgentContextProvider | None = None,
        generator: NpcAgentGenerator | None = None,
        repository: NpcAgentRepository | None = None,
    ) -> None:
        self._context_provider = context_provider or EmptyAgentContextProvider()
        self._generator = generator or QwenNpcAgentGenerator()
        self._repository = repository or InMemoryNpcAgentRepository()

    def generate_agent(
        self,
        query: str,
        contexts: list[SourceContext] | None = None,
    ) -> AgentGenerationResult:
        if not query.strip():
            raise ValueError("query must not be empty")

        resolved_contexts = list(contexts) if contexts is not None else self._context_provider.find_contexts(query)
        request = AgentGenerationRequest(query=query, contexts=resolved_contexts)
        result = self._generator.generate(request)
        self._repository.save(result.agent)
        return result

    def get_agent(self, agent_id: str) -> NpcAgentConfig | None:
        return self._repository.get_by_id(agent_id)

    def list_agents(self) -> list[NpcAgentConfig]:
        return self._repository.list_all()

    @classmethod
    def from_env(
        cls,
        context_provider: AgentContextProvider | None = None,
        repository: NpcAgentRepository | None = None,
    ) -> "NpcAgentService":
        """使用 .env 中的 OpenSearch 和 Qwen 配置构建服务。"""

        return cls(
            context_provider=context_provider or IterativeAgentContextProvider.from_env(),
            generator=QwenNpcAgentGenerator(),
            repository=repository,
        )
