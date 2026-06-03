"""用于 Qwen 的最小 DashScope OpenAI 兼容对话客户端。"""

from dataclasses import dataclass
import json
import socket
from typing import Any
from urllib import error, request

from src.config import QwenConfig


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

        try:
            with request.urlopen(req, timeout=self._config.timeout_seconds) as response:
                response_body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise QwenClientError(f"Qwen API HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise QwenClientError(f"Qwen API request failed: {exc.reason}") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise QwenClientError(
                f"Qwen API request timed out after {self._config.timeout_seconds} seconds"
            ) from exc

        try:
            data: dict[str, Any] = json.loads(response_body)
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise QwenClientError("Qwen API returned an invalid chat completion response") from exc
