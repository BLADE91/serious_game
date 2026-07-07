"""Use pa_backend ReAct agent as the script-generation backend.

The ReAct agent supports os_search (with collection_ids) for classic literature,
and w_baidu_search for real-world event retrieval.
"""

from dataclasses import dataclass
from http.client import HTTPSConnection, HTTPConnection, RemoteDisconnected
import json
import ssl
import threading
import time
from typing import Any, Callable
from urllib.parse import urlparse

from src.config import PABackendConfig
from src.generation.qwen_client import ChatMessage


class PABackendClientError(RuntimeError):
    """Raised when pa_backend cannot produce a usable stage response."""


@dataclass(frozen=True)
class _PABackendAuth:
    access_token: str
    user_id: str


class PABackendScriptClient:
    """Minimal ChatClient-compatible adapter for pa_backend ReAct agent.

    Uses the ReAct agent (/agent/os-search/react) which supports:
    - os_search with collection_ids constraint → classic literature
    - fetch_os_document_fulltext → read full documents from knowledge base
    - w_baidu_search → real-world event search
    """

    def __init__(
        self,
        config: PABackendConfig | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self._config = config or PABackendConfig.from_env()
        self._auth: _PABackendAuth | None = None
        self._conversation_id: str | None = None
        self._cancel_event = cancel_event
        self._active_conn: HTTPConnection | HTTPSConnection | None = None
        self._active_conn_lock = threading.Lock()
        self._search_scope_logged = False
        self._last_event_summary: dict[str, Any] = {}

    @property
    def conversation_id(self) -> str | None:
        return self._conversation_id

    def reset_conversation(self) -> None:
        """重置对话 ID，使下一次 complete() 调用创建全新的 Supabase 对话。

        用于 Call 3 逐章生成场景——每章应是独立的全新对话，
        避免前序章节的上下文污染当前章节的生成。
        """
        self._conversation_id = None

    def cancel_active_request(self) -> None:
        """Close any in-flight HTTP connection from another thread."""
        with self._active_conn_lock:
            conn = self._active_conn
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    def complete(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.2,
        stream_callback: Callable[..., None] | None = None,
    ) -> str:
        if self._cancel_event and self._cancel_event.is_set():
            raise PABackendClientError("生成已被用户取消")
        if not self._config.base_url:
            raise PABackendClientError("PA_BACKEND_BASE_URL is required")
        auth = self._ensure_auth()
        collection_ids = [self._config.collection_id] if self._config.collection_id else []
        attempts = max(0, self._config.max_stage_retries) + 1

        for attempt in range(attempts):
            if attempt:
                self.reset_conversation()
                delay = max(0, self._config.retry_backoff_seconds) * (2 ** (attempt - 1))
                if delay:
                    time.sleep(delay)

            conversation_id = self._ensure_conversation(
                auth, self._stage_title(messages),
            )
            payload = {
                "query": self._stage_prompt(messages),
                "collection_ids": collection_ids,
                "conversation_id": conversation_id,
                "search_preference": self._config.search_preference,
                "enable_web_search": True,
                "attachments_id": [],
                "oss_keys": [],
            }
            if not self._search_scope_logged:
                print(
                    "[pa_backend] search scope: "
                    f"collection_ids={json.dumps(collection_ids, ensure_ascii=False)}, "
                    "enable_web_search=true"
                )
                self._search_scope_logged = True
            if stream_callback is None:
                raw_content = self._post(
                    self._url(self._config.agent_endpoint), payload, auth.access_token
                )
                content = self._content_from_sse(raw_content)
            else:
                content = self._post(
                    self._url(self._config.agent_endpoint),
                    payload,
                    auth.access_token,
                    stream_callback=stream_callback,
                )
            if content.strip():
                return content
            if attempt < attempts - 1:
                print(
                    "[pa_backend] empty stage content; retrying with a new conversation "
                    f"({attempt + 1}/{attempts - 1}), events={self._last_event_summary}"
                )

        raise PABackendClientError(
            "pa_backend returned empty stage content "
            f"after {attempts} attempts; events={self._last_event_summary}"
        )

    def request_size_bytes(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.2,
    ) -> int:
        collection_ids = []
        if self._config.collection_id:
            collection_ids = [self._config.collection_id]
        payload = {
            "query": self._stage_prompt(messages),
            "collection_ids": collection_ids,
            "conversation_id": self._conversation_id or "<pending>",
            "search_preference": self._config.search_preference,
            "enable_web_search": True,
            "attachments_id": [],
            "oss_keys": [],
        }
        return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def _ensure_auth(self) -> _PABackendAuth:
        if self._auth is not None:
            return self._auth
        if not self._config.account or not self._config.password:
            raise PABackendClientError(
                "PA_BACKEND_ACCOUNT and PA_BACKEND_PASSWORD are required"
            )
        response = self._post_json(
            self._url(self._config.login_endpoint),
            {
                "account": self._config.account,
                "password": self._config.password,
            },
            token="",
        )
        token = str(response.get("access_token") or "").strip()
        user = response.get("user") if isinstance(response.get("user"), dict) else {}
        user_id = str(
            response.get("user_id")
            or response.get("id")
            or user.get("id")
            or ""
        ).strip()
        if not token:
            raise PABackendClientError("pa_backend login did not return access_token")
        if not user_id:
            raise PABackendClientError("pa_backend login did not return user id")
        self._auth = _PABackendAuth(access_token=token, user_id=user_id)
        return self._auth

    def _ensure_conversation(self, auth: _PABackendAuth, title: str) -> str:
        if self._conversation_id:
            return self._conversation_id
        if not self._config.supabase_url or not self._config.supabase_key:
            raise PABackendClientError(
                "PA_BACKEND_SUPABASE_URL and PA_BACKEND_SUPABASE_KEY are required"
            )
        response_text = self._post(
            f"{self._config.supabase_url}/rest/v1/conversations?select=id",
            [{"user_id": auth.user_id, "title": title[:40] or "剧本分步生成"}],
            token=auth.access_token,
            extra_headers={
                "apikey": self._config.supabase_key,
                "Prefer": "return=representation",
            },
        )
        try:
            parsed = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise PABackendClientError("Supabase returned invalid JSON") from exc
        if not isinstance(parsed, list) or not parsed:
            raise PABackendClientError("Supabase did not return created conversation")
        conversation_id = str(parsed[0].get("id") or "").strip()
        if not conversation_id:
            raise PABackendClientError("Supabase did not return conversation id")
        self._conversation_id = conversation_id
        return conversation_id

    def _post_json(self, url: str, payload: dict, token: str) -> dict:
        response = self._post(url, payload, token)
        try:
            parsed = json.loads(response)
        except json.JSONDecodeError as exc:
            raise PABackendClientError("pa_backend returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise PABackendClientError("pa_backend returned non-object JSON")
        return parsed

    def _post(
        self,
        url: str,
        payload: dict | list,
        token: str,
        extra_headers: dict[str, str] | None = None,
        stream_callback: Callable[..., None] | None = None,
    ) -> str:
        """Send POST request with http.client for cross-thread cancellation."""
        parsed = urlparse(url)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        path = parsed.path + ("?" + parsed.query if parsed.query else "")
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        headers = {"Content-Type": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
        if token:
            headers["Authorization"] = f"Bearer {token}"

        last_disconnect: RemoteDisconnected | None = None
        for attempt in range(2):
            if self._cancel_event and self._cancel_event.is_set():
                raise PABackendClientError("生成已被用户取消")

            conn: HTTPConnection | HTTPSConnection | None = None
            try:
                if parsed.scheme == "https":
                    ctx: ssl.SSLContext | None = None
                    if not self._config.verify_ssl:
                        ctx = ssl._create_unverified_context()
                    conn = HTTPSConnection(
                        host, port, timeout=self._config.timeout_seconds, context=ctx,
                    )
                else:
                    conn = HTTPConnection(host, port, timeout=self._config.timeout_seconds)

                with self._active_conn_lock:
                    self._active_conn = conn

                conn.request("POST", path, body, headers)
                response = conn.getresponse()
                if stream_callback is not None:
                    return self._read_sse_response(response, stream_callback)
                result = response.read().decode("utf-8", errors="replace")
                return result

            except (RemoteDisconnected, ConnectionError, OSError, TimeoutError) as exc:
                if self._cancel_event and self._cancel_event.is_set():
                    raise PABackendClientError("生成已被用户取消") from exc
                if isinstance(exc, RemoteDisconnected):
                    last_disconnect = exc
                    if attempt == 0:
                        continue
                raise PABackendClientError(f"pa_backend request failed: {exc}") from exc
            finally:
                with self._active_conn_lock:
                    if self._active_conn is conn:
                        self._active_conn = None
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

        raise PABackendClientError(
            "pa_backend request failed: remote server closed connection without response"
        ) from last_disconnect

    def _read_sse_response(
        self,
        response: Any,
        stream_callback: Callable[..., None],
    ) -> str:
        """Read a text/event-stream response incrementally and return content text."""
        content_parts: list[str] = []
        event_name = ""
        data_lines: list[str] = []
        last_content_len = 0
        event_counts: dict[str, int] = {}
        errors: list[str] = []
        recent_events: list[dict[str, Any]] = []

        while True:
            if self._cancel_event and self._cancel_event.is_set():
                raise PABackendClientError("生成已被用户取消")

            raw_line = response.readline()
            if raw_line == b"":
                break
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                progress_event = self._progress_event_from_sse(event_name, data_lines)
                self._consume_event(
                    event_name, data_lines, content_parts, event_counts, errors,
                )
                if progress_event:
                    recent_events.append(progress_event)
                    recent_events = recent_events[-8:]
                current_len = sum(len(part) for part in content_parts)
                if current_len != last_content_len:
                    last_content_len = current_len
                    self._notify_stream_callback(
                        stream_callback,
                        current_len,
                        {"event": "content", "message": f"已接收正文 {current_len / 1024:.1f} KiB"},
                    )
                elif progress_event:
                    self._notify_stream_callback(stream_callback, current_len, progress_event)
                event_name = ""
                data_lines = []
                continue
            if line.startswith("event:"):
                event_name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").strip())

        progress_event = self._progress_event_from_sse(event_name, data_lines)
        self._consume_event(event_name, data_lines, content_parts, event_counts, errors)
        if progress_event:
            recent_events.append(progress_event)
            recent_events = recent_events[-8:]
        self._last_event_summary = {
            "counts": event_counts,
            "errors": errors[-3:],
            "recent": recent_events[-5:],
        }
        current_len = sum(len(part) for part in content_parts)
        if current_len != last_content_len:
            self._notify_stream_callback(
                stream_callback,
                current_len,
                {"event": "content", "message": f"已接收正文 {current_len / 1024:.1f} KiB"},
            )
        elif progress_event:
            self._notify_stream_callback(stream_callback, current_len, progress_event)
        return "".join(content_parts).strip()

    def _content_from_sse(self, response_text: str) -> str:
        content_parts: list[str] = []
        event_name = ""
        data_lines: list[str] = []
        event_counts: dict[str, int] = {}
        errors: list[str] = []
        recent_events: list[dict[str, Any]] = []
        for raw_line in response_text.splitlines():
            line = raw_line.strip()
            if not line:
                progress_event = self._progress_event_from_sse(event_name, data_lines)
                self._consume_event(
                    event_name, data_lines, content_parts, event_counts, errors,
                )
                if progress_event:
                    recent_events.append(progress_event)
                    recent_events = recent_events[-8:]
                event_name = ""
                data_lines = []
                continue
            if line.startswith("event:"):
                event_name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").strip())
        progress_event = self._progress_event_from_sse(event_name, data_lines)
        self._consume_event(event_name, data_lines, content_parts, event_counts, errors)
        if progress_event:
            recent_events.append(progress_event)
        self._last_event_summary = {
            "counts": event_counts,
            "errors": errors[-3:],
            "recent": recent_events[-5:],
        }
        return "".join(content_parts).strip()

    def _notify_stream_callback(
        self,
        stream_callback: Callable[..., None],
        content_chars: int,
        event: dict[str, Any] | None = None,
    ) -> None:
        try:
            stream_callback(content_chars, event)
        except TypeError:
            stream_callback(content_chars)

    def _progress_event_from_sse(
        self,
        event_name: str,
        data_lines: list[str],
    ) -> dict[str, Any] | None:
        event_name = event_name.strip()
        if not event_name:
            return None
        raw_data = "\n".join(data_lines).strip()
        parsed: Any = raw_data
        if raw_data:
            try:
                parsed = json.loads(raw_data)
            except json.JSONDecodeError:
                parsed = raw_data
        message = self._summarize_event_message(event_name, parsed)
        if not message:
            return None
        return {
            "event": event_name,
            "message": message,
        }

    def _summarize_event_message(self, event_name: str, payload: Any) -> str:
        if event_name == "content":
            return ""
        if event_name in {"step", "status", "anchor"} and isinstance(payload, dict):
            stage = self._short_text(payload.get("stage"))
            message = self._short_text(payload.get("message"))
            if stage and message:
                return f"{stage}: {message}"
            return message or stage or event_name
        if event_name == "thought" and isinstance(payload, dict):
            label = self._short_text(payload.get("label"))
            stage = self._short_text(payload.get("stage"))
            if stage and label:
                return f"{stage}: {label}"
            return label or stage or "正在执行工具动作"
        if event_name == "tool_call" and isinstance(payload, dict):
            name = self._short_text(payload.get("display_label") or payload.get("name"))
            arguments = payload.get("arguments") or payload.get("input") or {}
            detail = self._tool_argument_summary(arguments)
            return f"调用工具 {name}{f'（{detail}）' if detail else ''}" if name else "调用工具"
        if event_name == "tool_result" and isinstance(payload, dict):
            name = self._short_text(payload.get("display_label") or payload.get("name"))
            preview = self._short_text(payload.get("preview"))
            status = "失败" if payload.get("is_error") else "完成"
            if name and preview:
                return f"工具 {name}{status}: {preview}"
            return f"工具 {name}{status}" if name else f"工具{status}"
        if event_name == "usage_update" and isinstance(payload, dict):
            parts = []
            for key, label in (
                ("billing_mode", "计费"),
                ("current_chain_tokens", "链路token"),
                ("included_tokens", "含免费token"),
                ("est_coins", "预估通晓币"),
            ):
                value = payload.get(key)
                if value not in (None, ""):
                    parts.append(f"{label}={value}")
            return "；".join(parts) if parts else "用量已更新"
        if event_name == "error":
            if isinstance(payload, dict):
                code = self._short_text(payload.get("code"))
                message = self._short_text(payload.get("message") or payload.get("detail"))
                return f"{code}: {message}" if code and message else message or code or "后端返回错误"
            return self._short_text(payload) or "后端返回错误"
        if event_name in {"doc", "web", "attachment_refs", "verify_results", "ads"}:
            count = len(payload) if isinstance(payload, list) else None
            label = {
                "doc": "知识库引用",
                "web": "网页引用",
                "attachment_refs": "附件引用",
                "verify_results": "核查结果",
                "ads": "广告推荐",
            }[event_name]
            return f"收到{count}条{label}" if count is not None else f"收到{label}"
        if event_name in {"done", "__end__"}:
            return "AgentX 本轮结束"
        if event_name == "usage_refund":
            return "本轮费用已退回"
        if event_name == "suggested_questions":
            return "收到后续问题建议"
        if event_name == "image":
            return "收到图片结果"
        if event_name == "evidence_package":
            return "收到追问/证据包状态"
        return f"收到 {event_name} 事件"

    def _tool_argument_summary(self, arguments: Any) -> str:
        if not isinstance(arguments, dict):
            return ""
        for key in ("query", "keyword", "url", "identifier", "collection_id", "skill_name"):
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                return f"{key}={self._short_text(value, 80)}"
        return ""

    def _short_text(self, value: Any, limit: int = 160) -> str:
        if value is None:
            return ""
        text = " ".join(str(value).split())
        if len(text) <= limit:
            return text
        return f"{text[:limit]}..."

    def _consume_event(
        self,
        event_name: str,
        data_lines: list[str],
        content_parts: list[str],
        event_counts: dict[str, int] | None = None,
        errors: list[str] | None = None,
    ) -> None:
        if event_name and event_counts is not None:
            event_counts[event_name] = event_counts.get(event_name, 0) + 1
        if event_name == "clarification":
            raise PABackendClientError(
                "ReAct Agent 意外触发追问（request_clarification），"
                "请检查 prompt 是否包含禁止追问的指令。"
            )
        if event_name == "error" and data_lines and errors is not None:
            errors.append("\n".join(data_lines)[:500])
            return
        if event_name != "content" or not data_lines:
            return
        data = "\n".join(data_lines)
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError:
            content_parts.append(data)
            return
        if isinstance(parsed, str):
            content_parts.append(parsed)
        elif isinstance(parsed, dict):
            for key in ("content", "text", "delta"):
                value = parsed.get(key)
                if isinstance(value, str):
                    content_parts.append(value)
                    break

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{self._config.base_url}{path}"

    def _stage_prompt(self, messages: list[ChatMessage]) -> str:
        sections = []
        for message in messages:
            sections.append(f"[{message.role}]\n{message.content}")
        prompt = "\n\n".join(sections)

        anti_clarification = (
            "绝对不要使用 request_clarification 追问用户。"
            "如果信息不足，基于常识和已有知识做出合理假设并直接给出结果。"
        )
        return f"{anti_clarification}\n\n{prompt}"

    def _stage_title(self, messages: list[ChatMessage]) -> str:
        for message in messages:
            if message.role == "system":
                return " ".join(message.content.split())[:40]
        return "剧本分步生成"
