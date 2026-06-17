"""剧本生成工作流服务。"""

from typing import Any
import os
import threading

from src.config import load_dotenv
from src.domain.script_design import ScriptGenerationRequest, ScriptGenerationResult
from src.domain.source_context import SourceContext
from src.generation.iterative_agent_context_provider import IterativeAgentContextProvider
from src.generation.opensearch_agent_context_provider import OpenSearchAgentContextProvider
from src.generation.retrieval_planner import QwenRetrievalPlanner
from src.generation.script_generator import GenerationProgressCallback, QwenScriptGenerator
from src.services.script_validator import ScriptValidator


class ScriptGenService:
    """编排 query 改写、资料检索和剧本初稿生成。"""

    def __init__(
        self,
        search_provider: OpenSearchAgentContextProvider | None = None,
        planner: QwenRetrievalPlanner | None = None,
        generator: QwenScriptGenerator | None = None,
        validator: ScriptValidator | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self._search_provider = search_provider  # None = lazy init when needed
        self._planner = planner or QwenRetrievalPlanner()
        self._generator = generator or QwenScriptGenerator(cancel_event=cancel_event)
        self._validator = validator or ScriptValidator()

    @property
    def _search(self) -> OpenSearchAgentContextProvider:
        """延迟初始化检索 provider，避免 PA Backend 模式下强制要求 OpenSearch。"""
        if self._search_provider is None:
            self._search_provider = OpenSearchAgentContextProvider.from_env()
        return self._search_provider

    def cancel_active_request(self) -> None:
        """取消正在进行的 HTTP 请求（委托给 generator）。"""
        if hasattr(self._generator, 'cancel_active_request'):
            self._generator.cancel_active_request()

    @classmethod
    def from_env(cls) -> "ScriptGenService":
        """使用 .env 构建剧本生成服务。"""

        return cls()

    def generate_script(
        self,
        request: ScriptGenerationRequest,
        progress_callback: GenerationProgressCallback | None = None,
    ) -> ScriptGenerationResult:
        query = self._effective_query(request)
        rewritten_queries = self.resolve_queries(request)
        if self._uses_pa_backend_generation() and not request.manual_queries:
            contexts = []
        else:
            contexts = self._search.find_contexts_for_queries(rewritten_queries)
            contexts = contexts[: request.max_contexts]
        if request.full_draft:
            script = self._generator.generate_full(
                query,
                contexts,
                request.feedback,
                progress_callback=progress_callback,
            )
            generation_notes = [
                "通过分阶段流水线生成完整结构化初稿。",
                "包含三幕结构、NPC 关系网、决策点序列和多结局条件。",
            ]
            generation_mode = "full"
        else:
            script = self._generator.generate(query, contexts, request.feedback)
            generation_notes = [
                "通过紧凑流水线生成剧本初稿。",
                "输出为小规模结构化草稿，便于快速迭代。",
            ]
            generation_mode = "compact"

        self._validator.validate(script, contexts, full_draft=request.full_draft)
        return ScriptGenerationResult(
            script=script,
            contexts_used=contexts,
            rewritten_queries=rewritten_queries,
            generation_notes=generation_notes,
            original_query=query,
            feedback=request.feedback,
            generation_mode=generation_mode,
        )

    def _effective_query(self, request: ScriptGenerationRequest) -> str:
        """根据请求构建有效 query。

        优先使用结构化字段拼接，否则回退到 query 字段。
        """
        if request.scenario.strip() and request.player_role.strip():
            parts = [
                f"基于{request.scenario}主题，生成一个严肃游戏《父母官》的剧本。",
                f"玩家扮演{request.player_role}。",
            ]
            if request.learning_goal.strip():
                parts.append(f"教育目标：{request.learning_goal}。")
            parts.append(
                f"目标游戏时长约{request.duration_minutes}分钟，"
                f"复杂度为{request.complexity}。"
            )
            if request.extra_requirements.strip():
                parts.append(f"额外要求：{request.extra_requirements}")
            return "".join(parts)
        return request.query.strip()

    def revise_script(
        self,
        previous_result: dict[str, Any],
        query: str,
        feedback: str,
    ) -> ScriptGenerationResult:
        """复用旧稿资料，根据人工反馈生成下一轮修订稿。"""

        previous_script = previous_result.get("script")
        if not isinstance(previous_script, dict) or not previous_script:
            raise ValueError("旧稿 JSON 缺少有效的 script 字段")
        if not feedback.strip():
            raise ValueError("修订剧本时 feedback 不能为空")

        contexts = self._build_contexts(previous_result.get("contexts_used"))
        rewritten_queries = self._string_list(previous_result.get("rewritten_queries"))
        original_query = previous_result.get("original_query")
        if not isinstance(original_query, str) or not original_query.strip():
            original_query = query

        previous_round = previous_result.get("revision_round", 0)
        if not isinstance(previous_round, int) or isinstance(previous_round, bool):
            previous_round = 0

        script = self._generator.revise(
            original_query,
            previous_script,
            contexts,
            feedback,
        )
        generation_mode = self._generation_mode(previous_result)
        self._validator.validate(script, contexts, full_draft=generation_mode == "full")
        return ScriptGenerationResult(
            script=script,
            contexts_used=contexts,
            rewritten_queries=rewritten_queries,
            generation_notes=[
                f"Revised from an existing draft using human feedback, round {previous_round + 1}.",
                "Reused the previous retrieval contexts without querying OpenSearch again.",
            ],
            original_query=original_query,
            feedback=feedback.strip(),
            revision_round=previous_round + 1,
            generation_mode=generation_mode,
        )

    def resolve_queries(self, request: ScriptGenerationRequest) -> list[str]:
        """解析或改写剧本检索 query，供 CLI 人工确认使用。"""

        manual_queries = [query.strip() for query in request.manual_queries if query.strip()]
        if manual_queries:
            return manual_queries[:3]

        query = self._effective_query(request)
        rewritten_queries = self._planner.rewrite_initial_queries(query)
        if rewritten_queries:
            return rewritten_queries[:3]
        return [query]

    def _uses_pa_backend_generation(self) -> bool:
        load_dotenv(override=False)
        return os.getenv("SCRIPT_GENERATION_BACKEND", "qwen").strip().lower() == "pa_backend"

    def _skip_retrieval(self, request: ScriptGenerationRequest) -> bool:
        """PA Backend 模式下不自行检索，由 agent 端处理。"""
        return self._uses_pa_backend_generation() and not request.manual_queries

    def _build_contexts(self, value: Any) -> list[SourceContext]:
        if not isinstance(value, list):
            return []

        contexts = []
        for item in value:
            if not isinstance(item, dict):
                continue
            context_id = item.get("id")
            title = item.get("title")
            content = item.get("content")
            metadata = item.get("metadata")
            if not all(isinstance(field, str) and field.strip() for field in (context_id, title, content)):
                continue
            contexts.append(
                SourceContext(
                    id=context_id,
                    title=title,
                    content=content,
                    metadata=metadata if isinstance(metadata, dict) else {},
                )
            )
        return contexts

    def _string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]

    def _generation_mode(self, previous_result: dict[str, Any]) -> str:
        mode = previous_result.get("generation_mode")
        if mode in {"compact", "full"}:
            return mode
        return "compact"
