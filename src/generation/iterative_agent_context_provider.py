"""受控的多步 Agent 资料检索 provider。"""

from src.config import IterativeRetrievalConfig
from src.domain.source_context import SourceContext
from src.generation.agent_context_provider import AgentContextProvider
from src.generation.opensearch_agent_context_provider import OpenSearchAgentContextProvider
from src.generation.retrieval_planner import QwenRetrievalPlanner, RetrievalPlan


class IterativeAgentContextProvider(AgentContextProvider):
    """先搜索，再由规划器决定是否改 query 或读取全文。"""

    def __init__(
        self,
        search_provider: OpenSearchAgentContextProvider | None = None,
        planner: QwenRetrievalPlanner | None = None,
        config: IterativeRetrievalConfig | None = None,
    ) -> None:
        self._config = config or IterativeRetrievalConfig.from_env()
        self._search_provider = search_provider or OpenSearchAgentContextProvider.from_env()
        self._planner = planner or QwenRetrievalPlanner(config=self._config)
        self.last_initial_queries: list[str] = []

    @classmethod
    def from_env(cls) -> "IterativeAgentContextProvider":
        """使用 .env 构建多步检索 provider。"""

        config = IterativeRetrievalConfig.from_env()
        return cls(
            search_provider=OpenSearchAgentContextProvider.from_env(),
            planner=QwenRetrievalPlanner(config=config),
            config=config,
        )

    def find_contexts(self, query: str) -> list[SourceContext]:
        if not query.strip():
            raise ValueError("query must not be empty")

        initial_queries = self._initial_queries(query)
        self.last_initial_queries = initial_queries
        contexts = self._search_provider.find_contexts_for_queries(initial_queries)
        searched_queries = {searched_query.lower() for searched_query in initial_queries}
        read_identifiers: set[str] = set()

        for round_index in range(self._config.max_rounds):
            plan = self._planner.plan(query, contexts, round_index)
            if plan.action == "done":
                break
            if plan.action == "requery":
                next_queries = self._next_queries(plan, searched_queries)
                if not next_queries:
                    break
                new_contexts = self._search_provider.find_contexts_for_queries(next_queries)
                contexts = self._merge_contexts(contexts, new_contexts)
                searched_queries.update(next_query.lower() for next_query in next_queries)
                continue
            if plan.action == "read_full_document":
                identifiers = self._next_identifiers(plan, contexts, read_identifiers)
                if not identifiers:
                    break
                full_contexts = self._search_provider.read_document_contexts(identifiers)
                contexts = self._merge_contexts(contexts, full_contexts)
                read_identifiers.update(identifiers)
                continue

        return contexts[: self._config.final_context_limit]

    def _initial_queries(self, query: str) -> list[str]:
        if not self._config.rewrite_initial_query:
            return [query.strip()]

        try:
            rewritten_queries = self._planner.rewrite_initial_queries(query)
        except Exception:
            return [query.strip()]

        if not rewritten_queries:
            return [query.strip()]
        return rewritten_queries[: self._config.max_queries_per_round]

    def _next_queries(self, plan: RetrievalPlan, searched_queries: set[str]) -> list[str]:
        next_queries = []
        for query in plan.queries:
            normalized = query.strip()
            if normalized and normalized.lower() not in searched_queries:
                next_queries.append(normalized)
            if len(next_queries) >= self._config.max_queries_per_round:
                break
        return next_queries

    def _next_identifiers(
        self,
        plan: RetrievalPlan,
        contexts: list[SourceContext],
        read_identifiers: set[str],
    ) -> list[str]:
        known_identifiers = {
            str(context.metadata.get("identifier", context.id)).strip()
            for context in contexts
            if str(context.metadata.get("identifier", context.id)).strip()
        }
        next_identifiers = []
        for identifier in plan.identifiers:
            normalized = identifier.strip()
            if (
                normalized
                and normalized in known_identifiers
                and normalized not in read_identifiers
            ):
                next_identifiers.append(normalized)
            if len(next_identifiers) >= self._config.max_full_documents:
                break
        return next_identifiers

    def _merge_contexts(
        self,
        existing_contexts: list[SourceContext],
        new_contexts: list[SourceContext],
    ) -> list[SourceContext]:
        merged_by_id = {context.id: context for context in existing_contexts}
        order = [context.id for context in existing_contexts]

        for context in new_contexts:
            if context.id not in merged_by_id:
                merged_by_id[context.id] = context
                order.append(context.id)
                continue

            current = merged_by_id[context.id]
            if len(context.content) > len(current.content):
                merged_by_id[context.id] = context

        return [merged_by_id[context_id] for context_id in order]
