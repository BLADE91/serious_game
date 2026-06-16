"""Use pa_backend agent as the staged script-generation backend."""

from dataclasses import dataclass
from http.client import RemoteDisconnected
import json
import ssl
from typing import Any
from urllib import error, request

from src.config import PABackendConfig
from src.generation.qwen_client import ChatMessage


class PABackendClientError(RuntimeError):
    """Raised when pa_backend cannot produce a usable stage response."""


@dataclass(frozen=True)
class _PABackendAuth:
    access_token: str
    user_id: str


class PABackendScriptClient:
    """Minimal ChatClient-compatible adapter for pa_backend OS agent.

    QwenScriptGenerator already breaks full-draft generation into seven
    independent JSON stages. This adapter keeps one pa_backend conversation for
    the whole draft and sends each stage prompt to that conversation.
    """

    def __init__(self, config: PABackendConfig | None = None) -> None:
        self._config = config or PABackendConfig.from_env()
        self._auth: _PABackendAuth | None = None
        self._conversation_id: str | None = None

    @property
    def conversation_id(self) -> str | None:
        return self._conversation_id

    def complete(self, messages: list[ChatMessage], temperature: float = 0.2) -> str:
        if not self._config.base_url:
            raise PABackendClientError("PA_BACKEND_BASE_URL is required")
        auth = self._ensure_auth()
        conversation_id = self._ensure_conversation(auth, self._stage_title(messages))
        payload = {
            "query": self._stage_prompt(messages),
            "collection_ids": [],
            "conversation_id": conversation_id,
            "search_preference": self._config.search_preference,
            "enable_web_search": self._config.enable_web_search,
            "attachments_id": [],
            "oss_keys": [],
        }
        response_text = self._post(self._url(self._config.agent_endpoint), payload, auth.access_token)
        content = self._content_from_sse(response_text)
        if not content.strip():
            raise PABackendClientError("pa_backend returned empty stage content")
        return content

    def request_size_bytes(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.2,
    ) -> int:
        payload = {
            "query": self._stage_prompt(messages),
            "collection_ids": [],
            "conversation_id": self._conversation_id or "<pending>",
            "search_preference": self._config.search_preference,
            "enable_web_search": self._config.enable_web_search,
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
            [{"user_id": auth.user_id, "title": title[:40] or "《父母官》分步剧本生成"}],
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
    ) -> str:
        last_disconnect: RemoteDisconnected | None = None
        for attempt in range(2):
            headers = {"Content-Type": "application/json"}
            if extra_headers:
                headers.update(extra_headers)
            if token:
                headers["Authorization"] = f"Bearer {token}"
            req = request.Request(
                url,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            try:
                if not self._config.verify_ssl and url.startswith("https://"):
                    response_cm = request.urlopen(
                        req,
                        timeout=self._config.timeout_seconds,
                        context=ssl._create_unverified_context(),
                    )
                else:
                    response_cm = request.urlopen(req, timeout=self._config.timeout_seconds)
                with response_cm as response:
                    return response.read().decode("utf-8", errors="replace")
            except error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                raise PABackendClientError(
                    f"pa_backend request failed: {exc.code} {detail}"
                ) from exc
            except error.URLError as exc:
                raise PABackendClientError(f"pa_backend request failed: {exc}") from exc
            except RemoteDisconnected as exc:
                last_disconnect = exc
                if attempt == 0:
                    continue
        raise PABackendClientError(
            "pa_backend request failed: remote server closed connection without response"
        ) from last_disconnect

    def _content_from_sse(self, response_text: str) -> str:
        content_parts: list[str] = []
        event_name = ""
        data_lines: list[str] = []
        for raw_line in response_text.splitlines():
            line = raw_line.strip()
            if not line:
                self._consume_event(event_name, data_lines, content_parts)
                event_name = ""
                data_lines = []
                continue
            if line.startswith("event:"):
                event_name = line.removeprefix("event:").strip()
            elif line.startswith("data:"):
                data_lines.append(line.removeprefix("data:").strip())
        self._consume_event(event_name, data_lines, content_parts)
        return "".join(content_parts).strip()

    def _consume_event(
        self,
        event_name: str,
        data_lines: list[str],
        content_parts: list[str],
    ) -> None:
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
        return (
            "你正在为严肃游戏《父母官》执行一个分阶段剧本生成任务。"
            "请检索并综合知识库/案例资料，但最终必须严格输出本阶段要求的合法 JSON 对象；"
            "不要输出 Markdown，不要解释 JSON 之外的内容。\n\n"
            + "\n\n".join(sections)
        )

    def _stage_title(self, messages: list[ChatMessage]) -> str:
        for message in messages:
            if message.role == "system":
                return " ".join(message.content.split())[:40]
        return "《父母官》分步剧本生成"
