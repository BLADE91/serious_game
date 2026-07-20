from __future__ import annotations

import json
from http.cookiejar import CookieJar
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen
from uuid import uuid4


class ApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "CLIENT_HTTP_ERROR",
        status: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.details = details or {}


class ApiClient:
    """只通过玩家 API 操作游戏；不导入或读取后端内部对象。"""

    TERMINAL_PROTOCOL_VERSION = "text-conversation-v2"

    def __init__(
        self,
        base_url: str,
        account_id: str,
        *,
        timeout: float = 15.0,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.account_id = account_id.strip()
        self.timeout = timeout
        self._cookie_jar = CookieJar()
        self._opener = opener or build_opener(
            HTTPCookieProcessor(self._cookie_jar)
        ).open
        self.csrf_token: str | None = None
        if not self.account_id:
            raise ValueError("account_id 不能为空")

    @staticmethod
    def new_key(prefix: str) -> str:
        return f"cli-{prefix}-{uuid4().hex}"

    def health(self) -> dict:
        return self._request("GET", "/health/live")

    def require_compatible_backend(self, health: dict) -> None:
        actual = str(health.get("terminal_protocol_version") or "")
        if actual != self.TERMINAL_PROTOCOL_VERSION:
            raise ApiError(
                "当前后端进程仍是旧版本，请先停止并重新运行 python run_server.py",
                code="BACKEND_RESTART_REQUIRED",
                details={
                    "expected": self.TERMINAL_PROTOCOL_VERSION,
                    "actual": actual or "missing",
                },
            )

    def readiness(self) -> dict:
        return self._request("GET", "/health/ready")

    def register(self, username: str, password: str) -> dict:
        result = self._request("POST", "/api/auth/register", {
            "username": username, "password": password,
        })
        self.csrf_token = str(result["csrf_token"])
        self.account_id = str(result["account_id"])
        return result

    def login(self, username: str, password: str) -> dict:
        result = self._request("POST", "/api/auth/login", {
            "username": username, "password": password,
        })
        self.csrf_token = str(result["csrf_token"])
        self.account_id = str(result["account_id"])
        return result

    def me(self) -> dict:
        return self._request("GET", "/api/auth/me")

    def logout(self) -> None:
        self._request("POST", "/api/auth/logout")
        self.csrf_token = None
        self._cookie_jar.clear()

    def new_session(
        self,
        *,
        origin_id: str,
        package_id: str | None = None,
        client_request_id: str | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "client_request_id": client_request_id or self.new_key("new"),
            "origin_id": origin_id,
        }
        if package_id:
            payload["package_id"] = package_id
        return self._request("POST", "/api/game/session", payload)

    def get_origins(self) -> dict:
        return self._request("GET", "/api/game/origins")

    def get_latest_active(self) -> dict:
        return self._request("GET", "/api/game/session/latest-active")

    def get_session(self, session_id: str) -> dict:
        return self._request("GET", self._session_path(session_id))

    def get_view(self, session_id: str, *, after: int = 0) -> dict:
        query = urlencode({"after": max(0, after)})
        return self._request("GET", f"{self._session_path(session_id)}/view?{query}")

    def get_feed(self, session_id: str, *, after: int = 0) -> dict:
        query = urlencode({"after": max(0, after)})
        return self._request("GET", f"{self._session_path(session_id)}/feed?{query}")

    def get_actions(self, session_id: str) -> dict:
        return self._request("GET", f"{self._session_path(session_id)}/actions")

    def get_opportunities(self, session_id: str) -> dict:
        return self._request("GET", f"{self._session_path(session_id)}/opportunities")

    def get_knowledge(self, session_id: str) -> dict:
        return self._request("GET", f"{self._session_path(session_id)}/knowledge")

    def get_map(self, session_id: str) -> dict:
        return self._request("GET", f"{self._session_path(session_id)}/map")

    def get_review(self, session_id: str) -> dict:
        return self._request("GET", f"{self._session_path(session_id)}/review")

    def get_package_validation(self) -> dict:
        return self._request("GET", "/api/game/package/validation")

    def submit_decision(
        self,
        session_id: str,
        *,
        state_version: int,
        decision_id: str,
        option_id: str | None = None,
        ordered_option_ids: list[str] | None = None,
        parameters: dict[str, Any] | None = None,
        client_action_id: str | None = None,
    ) -> dict:
        payload = {
                "input_mode": "decision",
                "client_action_id": client_action_id or self.new_key("decision"),
                "state_version": state_version,
                "decision_id": decision_id,
            }
        if option_id is not None:
            payload["option_id"] = option_id
        if ordered_option_ids:
            payload["ordered_option_ids"] = ordered_option_ids
        if parameters:
            payload["parameters"] = parameters
        return self._request(
            "POST",
            f"{self._session_path(session_id)}/action",
            payload,
        )

    def execute_tool(
        self,
        session_id: str,
        *,
        state_version: int,
        action_id: str,
        opportunity_id: str,
        client_action_id: str | None = None,
    ) -> dict:
        return self._request(
            "POST",
            f"{self._session_path(session_id)}/action",
            {
                "input_mode": "tool",
                "client_action_id": client_action_id or self.new_key("tool"),
                "state_version": state_version,
                "action_id": action_id,
                "opportunity_id": opportunity_id,
            },
        )

    def submit_free_text(
        self,
        session_id: str,
        *,
        state_version: int,
        opportunity_id: str,
        target_npc_id: str,
        conversation_id: str,
        player_text: str,
        client_action_id: str | None = None,
    ) -> dict:
        return self._request(
            "POST",
            f"{self._session_path(session_id)}/action",
            {
                "input_mode": "free_text",
                "client_action_id": client_action_id or self.new_key("talk"),
                "state_version": state_version,
                "opportunity_id": opportunity_id,
                "target_npc_id": target_npc_id,
                "conversation_id": conversation_id,
                "player_text": player_text,
            },
        )

    def start_conversation(
        self,
        session_id: str,
        *,
        state_version: int,
        opportunity_id: str,
        target_npc_id: str,
        client_action_id: str | None = None,
    ) -> dict:
        return self._request(
            "POST",
            f"{self._session_path(session_id)}/action",
            {
                "input_mode": "conversation_start",
                "client_action_id": client_action_id or self.new_key("conversation-start"),
                "state_version": state_version,
                "opportunity_id": opportunity_id,
                "target_npc_id": target_npc_id,
            },
        )

    def end_conversation(
        self,
        session_id: str,
        *,
        state_version: int,
        conversation_id: str,
        client_action_id: str | None = None,
    ) -> dict:
        return self._request(
            "POST",
            f"{self._session_path(session_id)}/action",
            {
                "input_mode": "conversation_end",
                "client_action_id": client_action_id or self.new_key("conversation-end"),
                "state_version": state_version,
                "conversation_id": conversation_id,
            },
        )

    def end_day(
        self,
        session_id: str,
        *,
        state_version: int,
        active_rest: bool = False,
        client_action_id: str | None = None,
    ) -> dict:
        return self._request(
            "POST",
            f"{self._session_path(session_id)}/end-day",
            {
                "client_action_id": client_action_id or self.new_key("end"),
                "state_version": state_version,
                "active_rest": active_rest,
            },
        )

    def get_operation(self, session_id: str, client_action_id: str) -> dict:
        key = quote(client_action_id, safe="")
        return self._request(
            "GET", f"{self._session_path(session_id)}/operations/{key}"
        )

    def _session_path(self, session_id: str) -> str:
        return f"/api/game/session/{quote(session_id, safe='')}"

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict:
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "X-Account-ID": self.account_id,
        }
        if self.csrf_token and method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            headers["X-CSRF-Token"] = self.csrf_token
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers=headers,
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            raw = exc.read()
            self._raise_response_error(raw, status=exc.code)
        except URLError as exc:
            raise ApiError(
                f"无法连接后端：{exc.reason}", code="CLIENT_CONNECTION_ERROR"
            ) from exc
        except TimeoutError as exc:
            raise ApiError("请求后端超时", code="CLIENT_TIMEOUT") from exc
        try:
            decoded = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError("后端返回了无法解析的响应") from exc
        if not isinstance(decoded, dict):
            raise ApiError("后端响应顶层必须是对象")
        return decoded

    @staticmethod
    def _raise_response_error(raw: bytes, *, status: int) -> None:
        try:
            decoded = json.loads(raw.decode("utf-8"))
            error = decoded.get("error", {})
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            error = {}
        raise ApiError(
            str(error.get("message") or f"后端请求失败（HTTP {status}）"),
            code=str(error.get("code") or "CLIENT_HTTP_ERROR"),
            status=status,
            details=error.get("details") if isinstance(error.get("details"), dict) else {},
        )
