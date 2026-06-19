"""剧本生成器 HTTP API —— FastAPI + SSE 进度推送。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.responses import FileResponse

from src.config import load_dotenv
from src.domain.script_design import ScriptGenerationRequest
from src.generation.script_generator import QwenScriptGenerator
from src.services.script_gen_service import ScriptGenService
from src.services.script_validator import ScriptValidationError


# ---- 任务追踪 ----

import secrets
import threading

_active_tasks: dict[str, dict] = {}
# 每个 task: {"event": threading.Event, "service": ScriptGenService | None}


def _create_task() -> str:
    task_id = secrets.token_hex(8)
    _active_tasks[task_id] = {"event": threading.Event(), "service": None}
    return task_id


def _cancel_task(task_id: str) -> bool:
    entry = _active_tasks.get(task_id)
    if entry is None:
        return False
    # 1. 设置取消标志
    entry["event"].set()
    # 2. 关闭正在进行的 HTTP 连接（立即中断 urlopen 阻塞）
    service = entry.get("service")
    if service is not None and hasattr(service, 'cancel_active_request'):
        service.cancel_active_request()
    return True


def _is_cancelled(task_id: str) -> bool:
    entry = _active_tasks.get(task_id)
    return entry is not None and entry["event"].is_set()


def _cleanup_task(task_id: str) -> None:
    _active_tasks.pop(task_id, None)

def _task_event(task_id: str) -> threading.Event | None:
    entry = _active_tasks.get(task_id)
    return entry["event"] if entry else None


class CancelledError(RuntimeError):
    """任务被用户取消。"""


# ---- 数据模型 ----

class GenerateRequest(BaseModel):
    scenario: str = Field(
        default="", description="政策场景，如 生态搬迁、征地拆迁",
    )
    player_role: str = Field(
        default="", description="玩家角色，如 乡镇党委副书记",
    )
    learning_goal: str = Field(
        default="", description="教育目标",
    )
    query: str = Field(
        default="", description="自由文本查询（兼容旧版，与结构化字段二选一）",
    )
    duration_minutes: int = Field(default=45, ge=10, le=120)
    extra_requirements: str = Field(default="")
    npc_count: int = Field(default=12, ge=8, le=20, description="NPC 数量")
    character_settings: str = Field(default="", description="人物设定")
    story_background: str = Field(default="", description="故事背景")
    feedback: str = Field(default="")
    full_draft: bool = Field(default=True)
    chapter_count: int = Field(default=6, ge=3, le=12, description="章节数量（章节式管线使用）")
    ending_count: int = Field(default=4, ge=2, le=8, description="结局数量（章节式管线使用）")


class ReviseRequest(BaseModel):
    previous_result: dict[str, Any] = Field(
        ..., description="上一轮的完整生成结果 JSON",
    )
    feedback: str = Field(
        ..., min_length=1, description="人工反馈",
    )
    query: str = Field(
        default="", description="原始 query（为空则从 previous_result 提取）",
    )


class ReviseElementRequest(BaseModel):
    element_type: str = Field(..., description="decision_point | npc | relationship | option | ending | act")
    element_id: str = Field(default="")
    current_element: dict[str, Any] = Field(..., description="当前元素完整 JSON")
    feedback: str = Field(..., min_length=1)
    context: dict[str, Any] = Field(default_factory=dict, description="相关上下文（NPC摘要、关系摘要等）")


class SaveManualRequest(BaseModel):
    result: dict[str, Any] = Field(..., description="完整的 lastResult 对象")
    source: str = Field(default="manual", description="manual | ai_element | ai_full")


class ExtractFromMDRequest(BaseModel):
    full_md: str = Field(..., min_length=1, description="完整 MD 剧本原文")
    npc_count: int = Field(default=12, ge=8, le=20)
    query: str = Field(default="", description="原始查询/需求描述")
    scenario: str = Field(default="")
    player_role: str = Field(default="")
    learning_goal: str = Field(default="")
    duration_minutes: int = Field(default=45)


# ---- SSE 工具 ----

async def sse_event(event: str, data: Any) -> str:
    """构建一条 SSE 消息。"""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def serialize_script(script: Any) -> dict[str, Any]:
    """将 ScriptDesign 转为 JSON 可序列化的字典。"""
    from dataclasses import asdict, is_dataclass

    def _convert(value: Any) -> Any:
        if is_dataclass(value) and not isinstance(value, type):
            return {key: _convert(val) for key, val in asdict(value).items()}
        if isinstance(value, list):
            return [_convert(item) for item in value]
        if isinstance(value, dict):
            return {str(key): _convert(val) for key, val in value.items()}
        return value

    return _convert(script)


# ---- 持久化 ----

OUTPUTS_DIR = Path(__file__).resolve().parent.parent.parent / "outputs" / "script_drafts"
COUNTER_FILE = OUTPUTS_DIR / ".version_counter"


def _load_counter() -> dict:
    """读取版本计数器。"""
    if COUNTER_FILE.exists():
        try:
            return json.loads(COUNTER_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"gen": 0, "rev": 0}


def _save_counter(counter: dict) -> None:
    """写入版本计数器。"""
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    COUNTER_FILE.write_text(json.dumps(counter), encoding="utf-8")


def _next_version(prefix: str) -> tuple[str, int, int]:
    """获取下一个版本号，返回 (文件名, gen, rev)。"""
    counter = _load_counter()
    if prefix == "script_generate":
        # 新的一代：gen+1, rev 归零
        gen = counter["gen"] + 1
        rev = 0
    else:
        # 同一代内的修订：gen 不变，rev+1
        gen = counter["gen"] if counter["gen"] > 0 else 1
        rev = counter["rev"] + 1
    counter["gen"] = gen
    counter["rev"] = rev
    _save_counter(counter)
    return f"v{gen:02d}-{rev:02d}_{prefix}", gen, rev


def _save_script_draft(script_dict: dict, prefix: str = "script_revision", full_md: str = "") -> str:
    """将剧本保存到 outputs/script_drafts/v{gen}/，返回文件名。

    Args:
        script_dict: 包含 script / contexts_used 等的完整结果字典
        prefix: 版本前缀
        full_md: MD 原文（可选，写入 saved JSON 的顶层字段）
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base, gen, rev = _next_version(prefix)
    version_dir = OUTPUTS_DIR / f"v{gen:02d}"
    version_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{base}_{ts}.json"
    filepath = version_dir / filename
    # 剔除前端传入的 filename 元数据，避免下次加载时覆盖真实文件名
    clean = {k: v for k, v in script_dict.items() if k != "filename"}
    if full_md:
        clean["full_md"] = full_md
    payload = json.dumps(clean, ensure_ascii=False, indent=2)
    filepath.write_text(payload, encoding="utf-8")
    return f"v{gen:02d}/{filename}"


# ---- 应用工厂 ----

def create_app() -> FastAPI:
    load_dotenv(override=True)

    app = FastAPI(
        title="剧本生成器",
        description="严肃游戏剧本结构化生成与修订 API",
        version="2.0.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---------- POST /api/generate ----------

    @app.post("/api/generate")
    async def generate(req: GenerateRequest):
        """提交生成请求，返回 SSE 流：progress → result。"""
        request = ScriptGenerationRequest(
            scenario=req.scenario,
            player_role=req.player_role,
            learning_goal=req.learning_goal,
            query=req.query,
            duration_minutes=req.duration_minutes,
            extra_requirements=req.extra_requirements,
            npc_count=req.npc_count,
            character_settings=req.character_settings,
            story_background=req.story_background,
            feedback=req.feedback,
            full_draft=req.full_draft,
        )

        task_id = _create_task()

        async def event_stream() -> AsyncGenerator[str, None]:
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[dict] = asyncio.Queue()

            def progress_callback(stage: int, total: int, name: str, request_bytes: int) -> None:
                """同步回调 → 异步队列，同时检测取消。"""
                if _is_cancelled(task_id):
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {"type": "cancelled", "message": "生成已被用户取消"},
                    )
                    return
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "progress", "stage": stage, "total": total,
                     "name": name, "request_bytes": request_bytes},
                )

            def run_generation() -> None:
                """在线程中运行同步生成。"""
                try:
                    if _is_cancelled(task_id):
                        return
                    cancel_evt = _task_event(task_id)
                    service = ScriptGenService(cancel_event=cancel_evt)
                    entry = _active_tasks.get(task_id)
                    if entry is not None:
                        entry["service"] = service
                    result = service.generate_script(request, progress_callback=progress_callback)
                    if _is_cancelled(task_id):
                        return
                    script_dict = serialize_script(result.script)
                    full_md = result.full_md
                    result_payload = {
                        "script": script_dict,
                        "full_md": full_md,
                        "contexts_used": [
                            {"id": c.id, "title": c.title, "content": c.content, "metadata": c.metadata}
                            for c in result.contexts_used
                        ],
                        "rewritten_queries": result.rewritten_queries,
                        "generation_notes": result.generation_notes,
                        "original_query": result.original_query,
                        "generation_mode": result.generation_mode,
                    }
                    # 自动保存到磁盘
                    saved_name = _save_script_draft(result_payload, prefix="script_generate", full_md=full_md)
                    result_payload["saved_as"] = saved_name
                    loop.call_soon_threadsafe(queue.put_nowait, {
                        "type": "result",
                        **result_payload,
                    })
                except ScriptValidationError as exc:
                    loop.call_soon_threadsafe(queue.put_nowait, {
                        "type": "error", "message": f"剧本校验失败：{exc.issues}", "issues": exc.issues,
                    })
                except Exception as exc:
                    loop.call_soon_threadsafe(queue.put_nowait, {
                        "type": "error", "message": f"生成失败：{exc}", "detail": traceback.format_exc(),
                    })

            import threading
            thread = threading.Thread(target=run_generation, daemon=True)
            thread.start()

            try:
                while True:
                    event_data = await queue.get()
                    event_type = event_data.pop("type")
                    yield await sse_event(event_type, event_data)
                    if event_type in ("result", "error", "cancelled"):
                        break
            except asyncio.CancelledError:
                _cancel_task(task_id)
                yield await sse_event("cancelled", {"message": "客户端断开连接"})
            finally:
                _cleanup_task(task_id)
                thread.join(timeout=5)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-Task-Id": task_id,
            },
        )

    # ---------- POST /api/cancel/{task_id} ----------

    @app.post("/api/cancel/{task_id}")
    async def cancel_task(task_id: str):
        """取消正在进行的生成任务。"""
        ok = _cancel_task(task_id)
        return {"cancelled": ok, "task_id": task_id}

    # ---------- POST /api/revise ----------

    @app.post("/api/revise")
    async def revise(req: ReviseRequest):
        """提交修订请求，返回 SSE 流：progress → result。"""
        task_id = _create_task()

        async def event_stream() -> AsyncGenerator[str, None]:
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[dict] = asyncio.Queue()

            def run_revision() -> None:
                try:
                    if _is_cancelled(task_id):
                        return
                    previous = req.previous_result
                    original_query = req.query.strip() or previous.get("original_query", "")
                    if not original_query:
                        loop.call_soon_threadsafe(queue.put_nowait, {
                            "type": "error", "message": "无法确定原始 query",
                        })
                        return

                    contexts_raw = previous.get("contexts_used", [])
                    from src.domain.source_context import SourceContext
                    contexts = [
                        SourceContext(
                            id=c["id"], title=c["title"],
                            content=c.get("content", ""),
                            metadata=c.get("metadata", {}),
                        )
                        for c in contexts_raw
                        if isinstance(c, dict) and c.get("id")
                    ]

                    from src.config import load_dotenv
                    import os
                    load_dotenv(override=False)
                    cancel_evt = _task_event(task_id)
                    generator = QwenScriptGenerator(cancel_event=cancel_evt)
                    entry = _active_tasks.get(task_id)
                    if entry is not None:
                        entry["service"] = generator

                    loop.call_soon_threadsafe(queue.put_nowait, {
                        "type": "progress", "stage": 1, "total": 1,
                        "name": "正在根据反馈修订剧本", "request_bytes": 0,
                    })

                    revised = generator.revise_structured(
                        original_query, previous, contexts, req.feedback,
                    )
                    script_dict = serialize_script(revised)
                    result_payload = {
                        "script": script_dict,
                        "contexts_used": previous.get("contexts_used", []),
                        "rewritten_queries": previous.get("rewritten_queries", []),
                        "generation_notes": [f"根据人工反馈修订：{req.feedback[:100]}"],
                        "original_query": original_query,
                        "generation_mode": "revision",
                    }
                    saved_name = _save_script_draft(result_payload, prefix="script_revise")
                    result_payload["saved_as"] = saved_name
                    loop.call_soon_threadsafe(queue.put_nowait, {
                        "type": "result",
                        **result_payload,
                    })
                except ScriptValidationError as exc:
                    loop.call_soon_threadsafe(queue.put_nowait, {
                        "type": "error", "message": f"修订后剧本校验失败：{exc.issues}", "issues": exc.issues,
                    })
                except Exception as exc:
                    loop.call_soon_threadsafe(queue.put_nowait, {
                        "type": "error", "message": f"修订失败：{exc}", "detail": traceback.format_exc(),
                    })

            import threading
            thread = threading.Thread(target=run_revision, daemon=True)
            thread.start()

            try:
                while True:
                    event_data = await queue.get()
                    event_type = event_data.pop("type")
                    yield await sse_event(event_type, event_data)
                    if event_type in ("result", "error", "cancelled"):
                        break
            except asyncio.CancelledError:
                _cancel_task(task_id)
                yield await sse_event("cancelled", {"message": "客户端断开连接"})
            finally:
                _cleanup_task(task_id)
                thread.join(timeout=5)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ---------- POST /api/generate-md ----------

    @app.post("/api/generate-md")
    async def generate_md(req: GenerateRequest):
        """Phase 1 only：生成 MD 剧本，SSE 返回 3 阶段进度 + MD 文本。

        生成后自动存盘（script 为 null，标记尚未提取结构化 JSON）。
        """
        request = ScriptGenerationRequest(
            scenario=req.scenario,
            player_role=req.player_role,
            learning_goal=req.learning_goal,
            query=req.query,
            duration_minutes=req.duration_minutes,
            extra_requirements=req.extra_requirements,
            npc_count=req.npc_count,
            character_settings=req.character_settings,
            story_background=req.story_background,
            feedback=req.feedback,
            full_draft=True,
        )

        task_id = _create_task()

        async def event_stream() -> AsyncGenerator[str, None]:
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[dict] = asyncio.Queue()

            def progress_callback(stage: int, total: int, name: str, request_bytes: int) -> None:
                if _is_cancelled(task_id):
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {"type": "cancelled", "message": "生成已被用户取消"},
                    )
                    return
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "progress", "stage": stage, "total": total,
                     "name": name, "request_bytes": request_bytes},
                )

            def run_generation() -> None:
                try:
                    if _is_cancelled(task_id):
                        return
                    cancel_evt = _task_event(task_id)
                    service = ScriptGenService(cancel_event=cancel_evt)
                    entry = _active_tasks.get(task_id)
                    if entry is not None:
                        entry["service"] = service
                    full_md, query = service.generate_md_only(request, progress_callback=progress_callback)
                    if _is_cancelled(task_id):
                        return
                    # 存盘（script 为 null，标记尚未提取）
                    result_payload = {
                        "script": None,
                        "full_md": full_md,
                        "contexts_used": [],
                        "rewritten_queries": [],
                        "generation_notes": ["MD 剧本原文（尚未提取结构化 JSON）"],
                        "original_query": query,
                        "generation_mode": "md_only",
                    }
                    saved_name = _save_script_draft(result_payload, prefix="script_generate", full_md=full_md)
                    result_payload["saved_as"] = saved_name
                    result_payload["script"] = None  # 确保序列化后为 null
                    loop.call_soon_threadsafe(queue.put_nowait, {
                        "type": "result",
                        **result_payload,
                    })
                except Exception as exc:
                    loop.call_soon_threadsafe(queue.put_nowait, {
                        "type": "error", "message": f"生成失败：{exc}", "detail": traceback.format_exc(),
                    })

            import threading
            thread = threading.Thread(target=run_generation, daemon=True)
            thread.start()

            try:
                while True:
                    event_data = await queue.get()
                    event_type = event_data.pop("type")
                    yield await sse_event(event_type, event_data)
                    if event_type in ("result", "error", "cancelled"):
                        break
            except asyncio.CancelledError:
                _cancel_task(task_id)
                yield await sse_event("cancelled", {"message": "客户端断开连接"})
            finally:
                _cleanup_task(task_id)
                thread.join(timeout=5)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-Task-Id": task_id,
            },
        )

    # ---------- POST /api/generate-chapter ----------

    @app.post("/api/generate-chapter")
    async def generate_chapter(req: GenerateRequest):
        """使用 6-Call 章节式管线生成剧本，SSE 流式返回 8 阶段进度。

        Call 1-3: PA Backend 创作（全局设定 → 章节大纲 → 逐章生成）
        Call 4-6: Qwen Flash 审校与抽取（一致性修订 → JSON 抽取 → 校验）
        """
        request = ScriptGenerationRequest(
            scenario=req.scenario,
            player_role=req.player_role,
            learning_goal=req.learning_goal,
            query=req.query,
            duration_minutes=req.duration_minutes,
            extra_requirements=req.extra_requirements,
            npc_count=req.npc_count,
            character_settings=req.character_settings,
            story_background=req.story_background,
            feedback=req.feedback,
            full_draft=True,
            chapter_count=req.chapter_count,
            ending_count=req.ending_count,
        )

        task_id = _create_task()

        async def event_stream() -> AsyncGenerator[str, None]:
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[dict] = asyncio.Queue()

            def progress_callback(stage: int, total: int, name: str, request_bytes: int) -> None:
                if _is_cancelled(task_id):
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {"type": "cancelled", "message": "生成已被用户取消"},
                    )
                    return
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "progress", "stage": stage, "total": total,
                     "name": name, "request_bytes": request_bytes},
                )

            def run_generation() -> None:
                try:
                    if _is_cancelled(task_id):
                        return
                    cancel_evt = _task_event(task_id)
                    service = ScriptGenService(cancel_event=cancel_evt)
                    entry = _active_tasks.get(task_id)
                    if entry is not None:
                        entry["service"] = service
                    result = service.generate_chapter_script(request, progress_callback=progress_callback)
                    if _is_cancelled(task_id):
                        return
                    script_dict = serialize_script(result.script)
                    full_md = result.full_md
                    result_payload = {
                        "script": script_dict,
                        "full_md": full_md,
                        "contexts_used": [],
                        "rewritten_queries": [],
                        "generation_notes": result.generation_notes,
                        "original_query": result.original_query,
                        "generation_mode": result.generation_mode,
                    }
                    saved_name = _save_script_draft(result_payload, prefix="script_generate", full_md=full_md)
                    result_payload["saved_as"] = saved_name
                    loop.call_soon_threadsafe(queue.put_nowait, {
                        "type": "result",
                        **result_payload,
                    })
                except Exception as exc:
                    loop.call_soon_threadsafe(queue.put_nowait, {
                        "type": "error", "message": f"生成失败：{exc}", "detail": traceback.format_exc(),
                    })

            import threading
            thread = threading.Thread(target=run_generation, daemon=True)
            thread.start()

            try:
                while True:
                    event_data = await queue.get()
                    event_type = event_data.pop("type")
                    yield await sse_event(event_type, event_data)
                    if event_type in ("result", "error", "cancelled"):
                        break
            except asyncio.CancelledError:
                _cancel_task(task_id)
                yield await sse_event("cancelled", {"message": "客户端断开连接"})
            finally:
                _cleanup_task(task_id)
                thread.join(timeout=5)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-Task-Id": task_id,
            },
        )

    # ---------- POST /api/extract-from-md ----------

    @app.post("/api/extract-from-md")
    async def extract_from_md(req: ExtractFromMDRequest):
        """Phase 2 only：从 MD 提取结构化 JSON，SSE 返回 5 阶段进度 + 完整结果。

        提取后存盘为新版本（同 gen，rev+1）。
        """
        task_id = _create_task()

        async def event_stream() -> AsyncGenerator[str, None]:
            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[dict] = asyncio.Queue()

            def progress_callback(stage: int, total: int, name: str, request_bytes: int) -> None:
                if _is_cancelled(task_id):
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {"type": "cancelled", "message": "提取已被用户取消"},
                    )
                    return
                loop.call_soon_threadsafe(
                    queue.put_nowait,
                    {"type": "progress", "stage": stage, "total": total,
                     "name": name, "request_bytes": request_bytes},
                )

            def run_extraction() -> None:
                try:
                    if _is_cancelled(task_id):
                        return
                    cancel_evt = _task_event(task_id)
                    service = ScriptGenService(cancel_event=cancel_evt)
                    entry = _active_tasks.get(task_id)
                    if entry is not None:
                        entry["service"] = service
                    # 构建 query
                    query = req.query.strip()
                    if not query:
                        parts = [req.scenario, req.player_role, req.learning_goal]
                        query = " ".join(p for p in parts if p)
                    script = service.extract_from_md(
                        full_md=req.full_md,
                        query=query,
                        npc_count=req.npc_count,
                        progress_callback=progress_callback,
                    )
                    if _is_cancelled(task_id):
                        return
                    script_dict = serialize_script(script)
                    result_payload = {
                        "script": script_dict,
                        "full_md": req.full_md,
                        "contexts_used": [],
                        "rewritten_queries": [],
                        "generation_notes": ["从 MD 剧本提取结构化 JSON。", "包含三幕结构、NPC 关系网、决策点序列和多结局条件。"],
                        "original_query": query,
                        "generation_mode": "full",
                    }
                    saved_name = _save_script_draft(result_payload, prefix="script_generate", full_md=req.full_md)
                    result_payload["saved_as"] = saved_name
                    loop.call_soon_threadsafe(queue.put_nowait, {
                        "type": "result",
                        **result_payload,
                    })
                except Exception as exc:
                    loop.call_soon_threadsafe(queue.put_nowait, {
                        "type": "error", "message": f"提取失败：{exc}", "detail": traceback.format_exc(),
                    })

            import threading
            thread = threading.Thread(target=run_extraction, daemon=True)
            thread.start()

            try:
                while True:
                    event_data = await queue.get()
                    event_type = event_data.pop("type")
                    yield await sse_event(event_type, event_data)
                    if event_type in ("result", "error", "cancelled"):
                        break
            except asyncio.CancelledError:
                _cancel_task(task_id)
                yield await sse_event("cancelled", {"message": "客户端断开连接"})
            finally:
                _cleanup_task(task_id)
                thread.join(timeout=5)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-Task-Id": task_id,
            },
        )

    # ---------- 版本管理 ----------

    @app.get("/api/versions")
    async def list_versions():
        """列出所有已保存的剧本版本。"""
        if not OUTPUTS_DIR.exists():
            return {"versions": []}
        versions = []

        for subdir in sorted(OUTPUTS_DIR.glob("v[0-9][0-9]"), reverse=True):
            if not subdir.is_dir():
                continue
            # 排除中间产物 JSON（0X_*.json），只列出最终结果
            json_candidates = [p for p in subdir.glob("*.json")
                               if not p.name.startswith("0")]
            for f in sorted(json_candidates, key=lambda p: p.stat().st_mtime, reverse=True):
                try:
                    with open(f, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                except (json.JSONDecodeError, OSError):
                    continue
                s = data.get("script") if isinstance(data, dict) else {}
                if not isinstance(s, dict):
                    s = {}
                rel_path = str(f.relative_to(OUTPUTS_DIR))
                gen_num = int(subdir.name[1:]) if subdir.name[1:].isdigit() else None
                # 从 JSON 读取 revision_round 和 generation_mode
                rev_num = data.get("revision_round", 0) if isinstance(data, dict) else 0
                mode = data.get("generation_mode", "") if isinstance(data, dict) else ""
                mode_labels = {"chapter": "章节式", "full": "三幕式·完整", "compact": "三幕式·紧凑"}
                mode_tag = mode_labels.get(mode, "")
                label = f"V{gen_num:02d}-{rev_num:02d}"
                if mode_tag:
                    label += f"（{mode_tag}）"
                versions.append({
                    "filename": rel_path,
                    "label": label,
                    "gen": gen_num,
                    "rev": rev_num,
                    "prefix": "script_generate",
                    "timestamp": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                    "title": s.get("title", "（剧本原文，尚未提取）") if s else "（剧本原文，尚未提取）",
                    "npc_count": len(s.get("npc_seed", [])),
                    "decision_count": len(s.get("decision_points", [])),
                    "chapters_count": len(s.get("chapters", [])),
                    "generation_mode": data.get("generation_mode", "") if isinstance(data, dict) else "",
                })
        return {"versions": versions}

    @app.get("/api/version/{filename:path}")
    async def load_version(filename: str):
        """加载指定版本的剧本（支持 v06/xxx.json 路径）。"""
        # 安全检查：禁止路径遍历
        safe = os.path.normpath(filename)
        if safe.startswith("..") or os.path.isabs(safe):
            raise HTTPException(400, "非法文件名")
        filepath = OUTPUTS_DIR / safe
        # 确保解析后的路径在 OUTPUTS_DIR 之内
        try:
            filepath.resolve().relative_to(OUTPUTS_DIR.resolve())
        except ValueError:
            raise HTTPException(400, "非法文件路径")
        if not filepath.exists():
            raise HTTPException(404, f"版本 {filename} 不存在")
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            raise HTTPException(500, f"读取版本文件失败：{exc}")
        if "script" not in data:
            data = {"script": data, "contexts_used": [], "rewritten_queries": [],
                    "generation_notes": ["从文件加载"], "original_query": "",
                    "generation_mode": "full"}
        data.pop("filename", None)  # 防御：避免 JSON 内嵌的旧 filename 覆盖
        return {"filename": filename, **data}

    # ---------- 局部元素修订 ----------

    @app.post("/api/revise-element")
    async def revise_element(req: ReviseElementRequest):
        """局部 AI 修订：只发目标元素 + 上下文，返回修改后的元素 JSON。"""
        from src.config import load_dotenv
        load_dotenv(override=False)
        cancel_evt = _task_event("")  # 短任务不需要取消
        generator = QwenScriptGenerator(cancel_event=cancel_evt)
        try:
            revised = generator.revise_element(
                element_type=req.element_type,
                element_id=req.element_id,
                current_element=req.current_element,
                context=req.context,
                feedback=req.feedback,
            )
            return {"element": revised}
        except Exception as exc:
            raise HTTPException(500, f"局部修订失败：{exc}")

    # ---------- 手动保存 ----------

    @app.post("/api/save-manual")
    async def save_manual(req: SaveManualRequest):
        """保存结果到服务端，source 区分来源。"""
        prefix_map = {"manual": "script_manual", "ai_element": "script_ai_element", "ai_full": "script_ai_full"}
        prefix = prefix_map.get(req.source, "script_manual")
        try:
            saved_name = _save_script_draft(req.result, prefix=prefix)
            return {"saved_as": saved_name, "ok": True, "source": req.source}
        except Exception as exc:
            raise HTTPException(500, f"保存失败：{exc}")

    # ---------- 加载已有结果 ----------

    @app.get("/api/latest-result")
    async def latest_result():
        """加载最近一次生成的剧本结果（用于前端直接预览和修订）。"""
        from pathlib import Path
        outputs_dir = Path(__file__).resolve().parent.parent.parent / "outputs" / "script_drafts"
        if not outputs_dir.exists():
            raise HTTPException(404, "outputs/script_drafts 目录不存在")
        # 排除中间产物 JSON（0X_*.json），只找最终结果
        json_candidates = [p for p in outputs_dir.glob("v[0-9][0-9]/*.json")
                           if not p.name.startswith("0")]
        json_files = sorted(json_candidates, key=lambda p: p.stat().st_mtime, reverse=True)
        if not json_files:
            raise HTTPException(404, "没有找到已生成的剧本")
        latest = json_files[0]
        try:
            with open(latest, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            raise HTTPException(500, f"读取剧本文件失败：{exc}")
        # 兼容旧格式：如果顶层没有 script 字段（或为 null），包装为完整格式
        if "script" not in data or data.get("script") is None:
            data = {"script": data, "contexts_used": [], "rewritten_queries": [],
                    "generation_notes": ["从文件加载"], "original_query": "",
                    "generation_mode": "full"}
        data.pop("filename", None)  # 防御：避免 JSON 内嵌的旧 filename 覆盖
        # 返回相对路径（含版本子目录）
        rel_path = latest.relative_to(outputs_dir)
        return {"filename": str(rel_path), **data}

    # ---------- 静态文件 ----------

    @app.get("/")
    async def index():
        """返回前端页面。"""
        from pathlib import Path
        frontend_path = Path(__file__).resolve().parent.parent.parent / "frontend" / "index.html"
        if frontend_path.exists():
            return FileResponse(str(frontend_path))
        return {"message": "剧本生成器 API", "version": "2.0.0", "docs": "/docs"}

    return app


app = create_app()
