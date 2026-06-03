"""基于 Qwen 的多步检索规划器。"""

from dataclasses import dataclass, field
import json
from typing import Any

from src.config import IterativeRetrievalConfig, QwenConfig
from src.domain.source_context import SourceContext
from src.generation.qwen_client import ChatMessage, QwenChatClient


class RetrievalPlannerError(RuntimeError):
    """当检索规划器无法解析模型输出时抛出。"""


@dataclass(frozen=True)
class RetrievalPlan:
    """一次检索后的下一步动作。"""

    action: str
    queries: list[str] = field(default_factory=list)
    identifiers: list[str] = field(default_factory=list)
    reason: str = ""


class QwenRetrievalPlanner:
    """让 Qwen 根据已检索资料决定是否继续检索。"""

    def __init__(
        self,
        client: QwenChatClient | None = None,
        config: IterativeRetrievalConfig | None = None,
    ) -> None:
        self._client = client or QwenChatClient(QwenConfig.from_env())
        self._config = config or IterativeRetrievalConfig.from_env()

    def plan(
        self,
        original_query: str,
        contexts: list[SourceContext],
        round_index: int,
    ) -> RetrievalPlan:
        content = self._client.complete(
            self._build_messages(original_query, contexts, round_index),
            temperature=0.1,
        )
        payload = self._parse_json_object(content)
        return self._build_plan(payload)

    def rewrite_initial_queries(self, original_query: str) -> list[str]:
        """把 Agent 生成任务改写为更适合检索的 query 列表。"""

        if not original_query.strip():
            raise ValueError("query must not be empty")

        content = self._client.complete(
            self._build_rewrite_messages(original_query),
            temperature=0.1,
        )
        payload = self._parse_json_object(content)
        queries = self._string_list(payload.get("queries"))
        return queries[: self._config.max_queries_per_round]

    def _build_messages(
        self,
        original_query: str,
        contexts: list[SourceContext],
        round_index: int,
    ) -> list[ChatMessage]:
        context_payload = [
            {
                "id": context.id,
                "title": context.title,
                "content_preview": self._preview(context.content),
                "identifier": context.metadata.get("identifier", context.id),
                "read_mode": context.metadata.get("read_mode", "search_result"),
            }
            for context in contexts[: self._config.final_context_limit]
        ]

        user_payload = {
            "original_query": original_query,
            "round_index": round_index,
            "retrieved_contexts": context_payload,
            "allowed_actions": ["done", "requery", "read_full_document"],
            "rules": [
                "如果资料已经足够生成 NPC Agent，action 使用 done。",
                "如果需要换关键词继续搜索，action 使用 requery，并给出最多 3 个 queries。",
                "如果某篇资料最相关但内容不够完整，action 使用 read_full_document，并给出最多 2 个 identifiers。",
                "只返回 JSON 对象，不要返回 Markdown。",
            ],
        }

        return [
            ChatMessage(
                role="system",
                content=(
                    "你是严肃游戏 NPC Agent 生成流程中的资料检索规划器。"
                    "你的任务是判断当前资料是否足够，并选择下一步受控检索动作。"
                    "必须只输出一个合法 JSON 对象。"
                ),
            ),
            ChatMessage(role="user", content=json.dumps(user_payload, ensure_ascii=False)),
        ]

    def _build_rewrite_messages(self, original_query: str) -> list[ChatMessage]:
        user_payload = {
            "original_query": original_query,
            "max_queries": self._config.max_queries_per_round,
            "rules": [
                "把生成 NPC Agent 的长任务描述改写成适合 OpenSearch 全文检索的关键词。",
                "优先保留制度场景、行为机制、角色身份、压力来源和治理领域。",
                "不要写完整句子，每个 query 应该是 2 到 6 个关键词组成的短语。",
                "最多返回 3 个 queries。",
                "只返回 JSON 对象，不要返回 Markdown。",
            ],
            "example_output": {
                "queries": [
                    "跨域水污染治理 府际博弈",
                    "环保督察 问责压力 地方政府",
                    "环保局 执法行为 信息上报",
                ],
                "reason": "将角色生成需求转为制度、行为和治理领域关键词。",
            },
        }

        return [
            ChatMessage(
                role="system",
                content=(
                    "你是严肃游戏 RAG 流程中的检索 query 改写器。"
                    "你的任务是把用户的 Agent 生成需求改写成少量高召回、高相关的检索关键词。"
                    "必须只输出一个合法 JSON 对象。"
                ),
            ),
            ChatMessage(role="user", content=json.dumps(user_payload, ensure_ascii=False)),
        ]

    def _preview(self, content: str) -> str:
        normalized = " ".join(content.split())
        if len(normalized) <= self._config.context_preview_chars:
            return normalized
        return f"{normalized[: self._config.context_preview_chars]}..."

    def _parse_json_object(self, content: str) -> dict[str, Any]:
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").strip()
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()

        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise RetrievalPlannerError("Qwen retrieval plan was not valid JSON") from exc

        if not isinstance(parsed, dict):
            raise RetrievalPlannerError("Qwen retrieval plan must be a JSON object")
        return parsed

    def _build_plan(self, payload: dict[str, Any]) -> RetrievalPlan:
        action = payload.get("action")
        if action not in {"done", "requery", "read_full_document"}:
            raise RetrievalPlannerError("Retrieval plan action must be done, requery, or read_full_document")

        queries = self._string_list(payload.get("queries"))[: self._config.max_queries_per_round]
        identifiers = self._string_list(payload.get("identifiers"))[: self._config.max_full_documents]
        reason = payload.get("reason", "")
        if not isinstance(reason, str):
            reason = ""

        return RetrievalPlan(
            action=action,
            queries=queries,
            identifiers=identifiers,
            reason=reason.strip(),
        )

    def _string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
