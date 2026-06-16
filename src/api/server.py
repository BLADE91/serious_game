"""父母官剧本生成器 HTTP API —— FastAPI + SSE 进度推送。"""

from __future__ import annotations

import asyncio
import json
import traceback
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
    complexity: str = Field(default="medium", pattern=r"^(simple|medium|complex)$")
    extra_requirements: str = Field(default="")
    feedback: str = Field(default="")
    full_draft: bool = Field(default=True)


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


# ---- 应用工厂 ----

def create_app() -> FastAPI:
    load_dotenv(override=True)

    app = FastAPI(
        title="父母官剧本生成器",
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
            complexity=req.complexity,
            extra_requirements=req.extra_requirements,
            feedback=req.feedback,
            full_draft=req.full_draft,
        )

        task_id = _create_task()

        async def event_stream() -> AsyncGenerator[str, None]:
            queue: asyncio.Queue[dict] = asyncio.Queue()

            def progress_callback(stage: int, total: int, name: str, request_bytes: int) -> None:
                """同步回调 → 异步队列，同时检测取消。"""
                if _is_cancelled(task_id):
                    try:
                        loop = asyncio.get_event_loop()
                        loop.call_soon_threadsafe(
                            queue.put_nowait,
                            {"type": "cancelled", "message": "生成已被用户取消"},
                        )
                    except RuntimeError:
                        pass
                    return
                try:
                    loop = asyncio.get_event_loop()
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {"type": "progress", "stage": stage, "total": total,
                         "name": name, "request_bytes": request_bytes},
                    )
                except RuntimeError:
                    pass

            def run_generation() -> None:
                """在线程中运行同步生成。"""
                try:
                    if _is_cancelled(task_id):
                        return
                    cancel_evt = _task_event(task_id)
                    service = ScriptGenService(cancel_event=cancel_evt)
                    # 保存引用以便取消端点关闭 HTTP 连接
                    entry = _active_tasks.get(task_id)
                    if entry is not None:
                        entry["service"] = service
                    result = service.generate_script(request, progress_callback=progress_callback)
                    if _is_cancelled(task_id):
                        return
                    script_dict = serialize_script(result.script)
                    final = {
                        "type": "result",
                        "script": script_dict,
                        "contexts_used": [
                            {"id": c.id, "title": c.title, "content": c.content, "metadata": c.metadata}
                            for c in result.contexts_used
                        ],
                        "rewritten_queries": result.rewritten_queries,
                        "generation_notes": result.generation_notes,
                        "original_query": result.original_query,
                        "generation_mode": result.generation_mode,
                    }
                    loop = asyncio.get_event_loop()
                    loop.call_soon_threadsafe(queue.put_nowait, final)
                except ScriptValidationError as exc:
                    loop = asyncio.get_event_loop()
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {"type": "error", "message": f"剧本校验失败：{exc.issues}", "issues": exc.issues},
                    )
                except Exception as exc:
                    loop = asyncio.get_event_loop()
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {"type": "error", "message": f"生成失败：{exc}", "detail": traceback.format_exc()},
                    )

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
            queue: asyncio.Queue[dict] = asyncio.Queue()

            def run_revision() -> None:
                try:
                    if _is_cancelled(task_id):
                        return
                    previous = req.previous_result
                    original_query = req.query.strip() or previous.get("original_query", "")
                    if not original_query:
                        loop = asyncio.get_event_loop()
                        loop.call_soon_threadsafe(
                            queue.put_nowait,
                            {"type": "error", "message": "无法确定原始 query"},
                        )
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
                    # 保存 generator 引用以便取消端点关闭 HTTP 连接
                    entry = _active_tasks.get(task_id)
                    if entry is not None:
                        entry["service"] = generator

                    loop = asyncio.get_event_loop()
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {"type": "progress", "stage": 1, "total": 1,
                         "name": "正在根据反馈修订剧本", "request_bytes": 0},
                    )

                    revised = generator.revise_structured(
                        original_query, previous, contexts, req.feedback,
                    )
                    script_dict = serialize_script(revised)
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {
                            "type": "result",
                            "script": script_dict,
                            "contexts_used": previous.get("contexts_used", []),
                            "rewritten_queries": previous.get("rewritten_queries", []),
                            "generation_notes": [
                                f"根据人工反馈修订：{req.feedback[:100]}",
                            ],
                            "original_query": original_query,
                            "generation_mode": "revision",
                        },
                    )
                except ScriptValidationError as exc:
                    loop = asyncio.get_event_loop()
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {"type": "error", "message": f"修订后剧本校验失败：{exc.issues}", "issues": exc.issues},
                    )
                except Exception as exc:
                    loop = asyncio.get_event_loop()
                    loop.call_soon_threadsafe(
                        queue.put_nowait,
                        {"type": "error", "message": f"修订失败：{exc}", "detail": traceback.format_exc()},
                    )

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

    # ---------- 静态文件 ----------

    @app.get("/")
    async def index():
        """返回前端页面。"""
        from pathlib import Path
        frontend_path = Path(__file__).resolve().parent.parent.parent / "frontend" / "index.html"
        if frontend_path.exists():
            return FileResponse(str(frontend_path))
        return {"message": "父母官剧本生成器 API", "version": "2.0.0", "docs": "/docs"}

    return app


app = create_app()
