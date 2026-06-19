"""通过 OpenAI Python SDK 调用 DashScope 兼容接口。"""

from dataclasses import dataclass
import json
from typing import Any

try:
    from openai import (
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
        DefaultHttpxClient,
        OpenAI,
    )
except ImportError:
    APIConnectionError = APIStatusError = APITimeoutError = None
    DefaultHttpxClient = None
    OpenAI = None

from src.config import QwenConfig


MAX_REQUEST_ATTEMPTS = 3


class QwenClientError(RuntimeError):
    """当 Qwen API 请求失败或返回无效响应时抛出。"""


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: str


class QwenChatClient:
    """使用 OpenAI SDK 调用 Qwen chat completions。"""

    def __init__(self, config: QwenConfig, client: Any | None = None) -> None:
        if client is None and OpenAI is None:
            raise QwenClientError(
                "openai Python SDK is required. Install it with: pip install openai"
            )

        self._config = config
        self._client = client
        if self._client is None:
            self._client = OpenAI(
                api_key=config.api_key,
                base_url=config.base_url,
                timeout=config.timeout_seconds,
                max_retries=MAX_REQUEST_ATTEMPTS - 1,
                http_client=DefaultHttpxClient(trust_env=False),
            )

    def complete(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.2,
        response_format: str = "json_object",
    ) -> str:
        payload = self._build_payload(messages, temperature, response_format)

        try:
            completion = self._client.chat.completions.create(**payload)
        except APITimeoutError as exc:
            raise QwenClientError(
                f"Qwen API request timed out after {self._config.timeout_seconds} seconds"
            ) from exc
        except APIConnectionError as exc:
            cause = self._root_cause(exc)
            raise QwenClientError(
                f"Qwen API connection failed: {exc}; root_cause={cause}"
            ) from exc
        except APIStatusError as exc:
            detail = self._status_error_detail(exc)
            raise QwenClientError(detail) from exc

        try:
            content = completion.choices[0].message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise QwenClientError("Qwen API returned an invalid chat completion response") from exc

        if not isinstance(content, str) or not content.strip():
            raise QwenClientError("Qwen API returned an empty chat completion")
        return content

    def request_size_bytes(
        self,
        messages: list[ChatMessage],
        temperature: float = 0.2,
        response_format: str = "json_object",
    ) -> int:
        """估算 OpenAI SDK 发送给 DashScope 的 JSON 请求体字节数。"""

        payload = self._build_payload(messages, temperature, response_format)
        serializable = {
            **payload,
            "extra_body": payload.get("extra_body", {}),
        }
        return len(json.dumps(serializable, ensure_ascii=False).encode("utf-8"))

    def _build_payload(
        self,
        messages: list[ChatMessage],
        temperature: float,
        response_format: str = "json_object",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in messages
            ],
            "temperature": temperature,
        }
        if response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}
        elif response_format == "text":
            # No response_format constraint — model outputs free text
            pass
        payload["extra_body"] = {"enable_thinking": False}
        return payload

    def _status_error_detail(self, exc: Any) -> str:
        status_code = getattr(exc, "status_code", "unknown")
        request_id = getattr(exc, "request_id", None)
        body = getattr(exc, "body", None)
        message = f"Qwen API HTTP {status_code}: {body or str(exc)}"
        if request_id:
            message = f"{message} (request_id={request_id})"
        return message

    def _root_cause(self, exc: BaseException) -> str:
        cause = exc
        while cause.__cause__ is not None:
            cause = cause.__cause__
        return repr(cause)
