"""用于 Qwen 的最小 DashScope OpenAI 兼容对话客户端。"""

from dataclasses import dataclass
import json
import socket
from typing import Any
from urllib import error, request

from src.config import QwenConfig


MAX_REQUEST_ATTEMPTS = 3


class QwenClientError(RuntimeError):
    """当 Qwen API 请求失败或返回无效响应时抛出。"""


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


class QwenChatClient:
    """基于标准库实现的 Qwen chat completions HTTP 客户端。"""

    def __init__(self, config: QwenConfig) -> None:
        self._config = config

    def complete(self, messages: list[ChatMessage], temperature: float = 0.2) -> str:
        payload = {
            "model": self._config.model,
            "messages": [message.__dict__ for message in messages],
            "temperature": temperature,
            "enable_thinking": False,
        }
        body = json.dumps(payload).encode("utf-8")
        endpoint = f"{self._config.base_url}/chat/completions"
        req = request.Request(
            endpoint,
            data=body,
            headers={
                "Authorization": f"Bearer {self._config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        response_body = self._perform_request(req)

        try:
            data: dict[str, Any] = json.loads(response_body)
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise QwenClientError("Qwen API returned an invalid chat completion response") from exc

    def _perform_request(self, req: request.Request) -> str:
        last_error: Exception | None = None

        for attempt in range(1, MAX_REQUEST_ATTEMPTS + 1):
            try:
                with request.urlopen(req, timeout=self._config.timeout_seconds) as response:
                    return response.read().decode("utf-8")
            except error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                if exc.code != 429 and exc.code < 500:
                    raise QwenClientError(f"Qwen API HTTP {exc.code}: {detail}") from exc
                last_error = QwenClientError(f"Qwen API HTTP {exc.code}: {detail}")
            except error.URLError as exc:
                last_error = QwenClientError(f"Qwen API request failed: {exc.reason}")
            except (TimeoutError, socket.timeout) as exc:
                last_error = QwenClientError(
                    f"Qwen API request timed out after {self._config.timeout_seconds} seconds"
                )

            if attempt == MAX_REQUEST_ATTEMPTS:
                break

        raise QwenClientError(
            f"Qwen API request failed after {MAX_REQUEST_ATTEMPTS} attempts: {last_error}"
        ) from last_error
