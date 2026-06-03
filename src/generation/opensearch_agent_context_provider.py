"""基于 OpenSearch 的 Agent 资料上下文提供器。"""

from typing import Any

from src.config import OpenSearchConfig
from src.domain.source_context import SourceContext
from src.generation.agent_context_provider import AgentContextProvider
from src.generation.opensearch_client import OpenSearchSourceClient


class OpenSearchAgentContextProvider(AgentContextProvider):
    """从 OpenSearch 中检索与 Agent 生成 query 相关的资料。"""

    def __init__(
        self,
        config: OpenSearchConfig | None = None,
        client: OpenSearchSourceClient | None = None,
    ) -> None:
        self._config = config or OpenSearchConfig.from_env()
        self._client = client or OpenSearchSourceClient(self._config)

    @classmethod
    def from_env(cls) -> "OpenSearchAgentContextProvider":
        """使用 .env 中的 OpenSearch 配置构建 provider。"""

        return cls(config=OpenSearchConfig.from_env())

    def find_contexts(self, query: str) -> list[SourceContext]:
        return self.find_contexts_for_queries([query])

    def find_contexts_for_queries(self, queries: list[str]) -> list[SourceContext]:
        """根据一组 query 执行 breadth 检索。"""

        queries = self._normalize_queries(queries)
        if not queries:
            raise ValueError("query must not be empty")

        body = self._build_search_body(queries)
        response = self._client.search(body)
        return self._to_source_contexts(response)

    def read_document_contexts(self, identifiers: list[str]) -> list[SourceContext]:
        """按文档 identifier 读取更多 chunk 内容。"""

        valid_identifiers = [identifier.strip() for identifier in identifiers if identifier and identifier.strip()]
        if not valid_identifiers:
            return []

        body = self._build_document_read_body(valid_identifiers)
        response = self._client.search(body)
        return self._plain_hits_to_document_contexts(response)

    def _normalize_queries(self, queries: list[str]) -> list[str]:
        valid_queries = [query.strip() for query in queries if query and query.strip()]
        return valid_queries[:3]

    def _build_search_body(self, queries: list[str]) -> dict[str, Any]:
        query_clause = self._build_multiquery_clause(queries)
        content_field = self._config.content_field

        body: dict[str, Any] = {
            "track_total_hits": self._config.top_k,
            "_source": {
                "excludes": [
                    f"{content_field}.shingle",
                    f"{content_field}.en",
                    f"{content_field}.ngram",
                ]
            },
            "size": self._config.top_k,
            "query": {"bool": {"must": [query_clause], "filter": []}},
            "sort": [{"_score": {"order": "desc"}}],
            "collapse": {
                "field": self._config.identifier_field,
                "inner_hits": {
                    "name": "chunks",
                    "size": self._config.inner_hits_size,
                    "sort": [{"_score": {"order": "desc"}}],
                    "_source": [
                        self._config.identifier_field,
                        self._config.title_field,
                        self._config.content_field,
                    ],
                },
            },
        }
        return body

    def _build_document_read_body(self, identifiers: list[str]) -> dict[str, Any]:
        source_fields = [
            self._config.identifier_field,
            self._config.title_field,
            self._config.content_field,
            "id",
            "id_all",
            self._config.chunk_order_field,
        ]
        body: dict[str, Any] = {
            "track_total_hits": self._config.full_document_chunk_limit,
            "_source": source_fields,
            "size": self._config.full_document_chunk_limit,
            "query": {
                "bool": {
                    "filter": [
                        {"terms": {self._config.identifier_field: identifiers}},
                    ]
                }
            },
        }

        if self._config.chunk_order_field:
            body["sort"] = [
                {
                    self._config.chunk_order_field: {
                        "order": "asc",
                        "unmapped_type": "long",
                    }
                }
            ]

        return body

    def _build_multiquery_clause(self, queries: list[str]) -> dict[str, Any]:
        should_clauses = []
        for query in queries:
            match_part: dict[str, Any] = {
                "simple_query_string": {
                    "query": query,
                    "fields": list(self._config.search_fields),
                    "default_operator": "OR",
                }
            }

            lang = self._detect_lang_filter(query)
            if lang == "en":
                should_clauses.append(
                    {
                        "bool": {
                            "must": [match_part],
                            "filter": [{"term": {"lang": lang}}],
                        }
                    }
                )
            else:
                should_clauses.append(match_part)

        return {
            "bool": {
                "should": should_clauses,
                "minimum_should_match": 1,
            }
        }

    def _detect_lang_filter(self, query: str) -> str | None:
        has_cjk = any("\u4e00" <= char <= "\u9fff" for char in query)
        has_ascii_alpha = any(char.isascii() and char.isalpha() for char in query)
        if has_ascii_alpha and not has_cjk:
            return "en"
        return None

    def _to_source_contexts(self, response: dict[str, Any]) -> list[SourceContext]:
        hits = response.get("hits", {}).get("hits", [])
        contexts = []
        for hit in hits:
            context = self._hit_to_source_context(hit)
            if context is not None:
                contexts.append(context)
        return contexts

    def _plain_hits_to_document_contexts(self, response: dict[str, Any]) -> list[SourceContext]:
        hits = response.get("hits", {}).get("hits", [])
        grouped_sources: dict[str, list[dict[str, Any]]] = {}
        scores: dict[str, float | None] = {}
        indices: dict[str, str | None] = {}

        for hit in hits:
            source = hit.get("_source", {}) or {}
            identifier = str(source.get(self._config.identifier_field) or hit.get("_id") or "").strip()
            if not identifier:
                continue
            grouped_sources.setdefault(identifier, []).append(source)
            scores.setdefault(identifier, hit.get("_score"))
            indices.setdefault(identifier, hit.get("_index"))

        contexts = []
        for identifier, sources in grouped_sources.items():
            title = self._first_non_empty(sources, self._config.title_field) or "未命名资料"
            content_parts = [
                str(source.get(self._config.content_field) or "").strip()
                for source in sources
                if str(source.get(self._config.content_field) or "").strip()
            ]
            if not content_parts:
                continue

            metadata = {
                "source": "opensearch",
                "read_mode": "full_document_chunks",
                "index": indices.get(identifier),
                "score": scores.get(identifier),
                "identifier": identifier,
                "chunk_ids": [
                    source.get("id_all") or source.get("id") or source.get(self._config.identifier_field)
                    for source in sources
                ],
            }
            contexts.append(
                SourceContext(
                    id=identifier,
                    title=title,
                    content="\n\n".join(content_parts),
                    metadata=metadata,
                )
            )

        return contexts

    def _hit_to_source_context(self, hit: dict[str, Any]) -> SourceContext | None:
        source = hit.get("_source", {}) or {}
        identifier = str(source.get(self._config.identifier_field) or hit.get("_id") or "").strip()
        title = str(source.get(self._config.title_field) or "未命名资料").strip()
        chunks = self._extract_chunk_sources(hit)
        content = self._build_content(source, chunks)

        if not identifier or not content:
            return None

        metadata = {
            "source": "opensearch",
            "index": hit.get("_index"),
            "score": hit.get("_score"),
            "identifier": identifier,
            "chunk_ids": [
                chunk.get("id_all") or chunk.get("id") or chunk.get(self._config.identifier_field)
                for chunk in chunks
            ],
        }

        return SourceContext(
            id=identifier,
            title=title,
            content=content,
            metadata=metadata,
        )

    def _extract_chunk_sources(self, hit: dict[str, Any]) -> list[dict[str, Any]]:
        inner_hits = hit.get("inner_hits", {})
        chunk_hits = inner_hits.get("chunks", {}).get("hits", {}).get("hits", [])
        return [chunk_hit.get("_source", {}) or {} for chunk_hit in chunk_hits]

    def _build_content(self, source: dict[str, Any], chunks: list[dict[str, Any]]) -> str:
        content_parts = []
        for chunk in chunks:
            content = str(chunk.get(self._config.content_field) or "").strip()
            if content:
                content_parts.append(content)

        if not content_parts:
            source_content = str(source.get(self._config.content_field) or "").strip()
            if source_content:
                content_parts.append(source_content)

        return "\n\n".join(content_parts)

    def _first_non_empty(self, sources: list[dict[str, Any]], field: str) -> str:
        for source in sources:
            value = str(source.get(field) or "").strip()
            if value:
                return value
        return ""
