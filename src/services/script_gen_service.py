"""剧本生成工作流服务。"""

from typing import Any
import os
import threading

from src.config import load_dotenv
from src.domain.script_design import ScriptDesign, ScriptGenerationRequest, ScriptGenerationResult
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
            script, full_md = self._generator.generate_full(
                query,
                contexts,
                request.feedback,
                progress_callback=progress_callback,
                npc_count=request.npc_count,
                character_settings=request.character_settings,
                story_background=request.story_background,
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
            full_md = ""

        self._validator.validate(script, contexts, full_draft=request.full_draft)
        return ScriptGenerationResult(
            script=script,
            full_md=full_md,
            contexts_used=contexts,
            rewritten_queries=rewritten_queries,
            generation_notes=generation_notes,
            original_query=query,
            feedback=request.feedback,
            generation_mode=generation_mode,
        )

    def generate_md_only(
        self,
        request: ScriptGenerationRequest,
        progress_callback: GenerationProgressCallback | None = None,
    ) -> tuple[str, str]:
        """Phase 1 only：生成 MD 剧本，返回 (full_md, query)。

        MD 生成后立即存盘（script 字段为 null，标记尚未提取）。
        """
        query = self._effective_query(request)
        rewritten_queries = self.resolve_queries(request)
        if self._uses_pa_backend_generation() and not request.manual_queries:
            contexts = []
        else:
            contexts = self._search.find_contexts_for_queries(rewritten_queries)
            contexts = contexts[: request.max_contexts]

        full_md = self._generator.generate_md(
            query,
            contexts,
            request.feedback,
            progress_callback=progress_callback,
            npc_count=request.npc_count,
            character_settings=request.character_settings,
            story_background=request.story_background,
        )
        return full_md, query

    def extract_from_md(
        self,
        full_md: str,
        query: str,
        npc_count: int,
        progress_callback: GenerationProgressCallback | None = None,
    ) -> ScriptDesign:
        """Phase 2 only：从 MD 提取结构化 JSON。"""
        script = self._generator.extract_json_from_md(
            full_md,
            query=query,
            npc_count=npc_count,
            progress_callback=progress_callback,
        )
        return script

    def generate_chapter_script(
        self,
        request: ScriptGenerationRequest,
        progress_callback: GenerationProgressCallback | None = None,
        output_dir: str | None = None,
    ) -> ScriptGenerationResult:
        """使用新的 6-Call 章节式管线生成剧本。

        Call 1-3 使用 PA Backend（创作），Call 4-6 使用 Qwen Flash（审校与抽取）。

        Args:
            output_dir: 如果提供，每步中间产物（设定、大纲、各章、修订前后…）写入此目录
        """
        from src.generation.chapter_script_generator import ChapterScriptGenerator

        chapter_generator = ChapterScriptGenerator(cancel_event=self._get_cancel_event())
        script, full_md = chapter_generator.generate_full(
            request, progress_callback, output_dir=output_dir,
        )

        return ScriptGenerationResult(
            script=script,
            full_md=full_md,
            contexts_used=[],
            rewritten_queries=[],
            generation_notes=[
                "通过 6 步章节式管线生成完整剧本。",
                "Call 1-3: PA Backend 负责创作（全局设定 → 章节大纲 → 逐章生成）。",
                "Call 4-6: Qwen Flash 负责审校与抽取（一致性修订 → JSON 抽取 → 校验）。",
            ],
            original_query=self._effective_query(request),
            feedback=request.feedback,
            generation_mode="chapter",
        )

    def _get_cancel_event(self) -> threading.Event | None:
        """获取取消事件（如果 generator 有的话）。"""
        if hasattr(self._generator, '_cancel_event'):
            return self._generator._cancel_event
        return None

    def _effective_query(self, request: ScriptGenerationRequest) -> str:
        """根据请求构建有效 query。

        优先使用结构化字段拼接，否则回退到 query 字段。
        """
        if request.scenario.strip() and request.player_role.strip():
            parts = [
                f"基于{request.scenario}主题，生成一个严肃游戏的剧本。",
                f"玩家扮演{request.player_role}。",
            ]
            if request.learning_goal.strip():
                parts.append(f"教育目标：{request.learning_goal}。")
            parts.append(
                f"目标游戏时长约{request.duration_minutes}分钟。"
            )
            if request.npc_count:
                parts.append(f"NPC数量约{request.npc_count}人。")
            if request.character_settings.strip():
                parts.append(f"人物设定：{request.character_settings}")
            if request.story_background.strip():
                parts.append(f"故事背景：{request.story_background}")
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
            full_md=previous_result.get("full_md", ""),
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
