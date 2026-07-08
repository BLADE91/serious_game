"""章节式剧本生成工作流服务。"""

from typing import Callable
import threading

from src.domain.script_design import ScriptGenerationRequest, ScriptGenerationResult

GenerationProgressCallback = Callable[[int, int, str, int], None]


class ScriptGenService:
    """编排当前章节式剧本生成管线。"""

    def __init__(
        self,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self._cancel_event = cancel_event
        self._active_generator = None

    def cancel_active_request(self) -> None:
        """取消正在进行的章节式生成请求。"""
        generator = self._active_generator
        pa_client = getattr(generator, "_pa_client", None)
        if hasattr(pa_client, "cancel_active_request"):
            pa_client.cancel_active_request()

    def generate_chapter_script(
        self,
        request: ScriptGenerationRequest,
        progress_callback: GenerationProgressCallback | None = None,
        output_dir: str | None = None,
    ) -> ScriptGenerationResult:
        """使用 6-Call 章节式管线生成剧本。"""
        from src.generation.chapter_script_generator import ChapterScriptGenerator

        chapter_generator = ChapterScriptGenerator(cancel_event=self._cancel_event)
        self._active_generator = chapter_generator
        try:
            script, full_md = chapter_generator.generate_full(
                request, progress_callback, output_dir=output_dir,
            )
        finally:
            self._active_generator = None

        return ScriptGenerationResult(
            script=script,
            full_md=full_md,
            contexts_used=[],
            rewritten_queries=[],
            generation_notes=[
                "通过章节式管线生成完整剧本。",
            ],
            original_query=self._effective_query(request),
            feedback=request.feedback,
            generation_mode="chapter",
        )

    def _effective_query(self, request: ScriptGenerationRequest) -> str:
        """根据结构化字段构建有效 query，兼容仅传 query 的 CLI/测试调用。"""
        if request.scenario.strip() and request.player_role.strip():
            parts = [
                f"基于{request.scenario}主题，生成一个严肃游戏的剧本。",
                f"玩家扮演{request.player_role}。",
            ]
            if request.learning_goal.strip():
                parts.append(f"教育目标：{request.learning_goal}。")
            parts.append(f"目标游戏时长约{request.duration_minutes}分钟。")
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
