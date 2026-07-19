from __future__ import annotations

from io import BytesIO
import json
import unittest
from urllib.error import HTTPError

from terminal_client.api_client import ApiClient, ApiError


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class ApiClientTests(unittest.TestCase):
    def test_register_sets_account_and_csrf_for_next_write(self) -> None:
        requests = []

        def opener(request, *, timeout):
            requests.append(request)
            if request.full_url.endswith("/api/auth/register"):
                return FakeResponse({
                    "account_id": "acct_local", "roles": ["player"],
                    "csrf_token": "csrf-local", "expires_at": "later",
                })
            return FakeResponse({"session_id": "game_1", "state_version": 1})

        client = ApiClient("http://example.test", "terminal-local", opener=opener)
        registered = client.register("local-user", "correct horse battery")
        client.new_session(origin_id="technical")

        self.assertEqual("acct_local", registered["account_id"])
        self.assertEqual("acct_local", client.account_id)
        self.assertEqual("csrf-local", requests[1].get_header("X-csrf-token"))

    def test_new_session_sends_origin_and_idempotency_key(self) -> None:
        captured = {}

        def opener(request, *, timeout):
            captured["request"] = request
            return FakeResponse({"session_id": "game_1", "state_version": 1})

        client = ApiClient("http://example.test", "acct_terminal", opener=opener)
        result = client.new_session(origin_id="technical")

        self.assertEqual("game_1", result["session_id"])
        payload = json.loads(captured["request"].data.decode("utf-8"))
        self.assertEqual("technical", payload["origin_id"])
        self.assertTrue(payload["client_request_id"].startswith("cli-new-"))

    def test_decision_request_uses_visible_contract_and_account_header(self) -> None:
        captured = {}

        def opener(request, *, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse({"state_version": 2, "status": "succeeded"})

        client = ApiClient(
            "http://example.test/",
            "acct_terminal",
            timeout=3.5,
            opener=opener,
        )
        result = client.submit_decision(
            "game/unsafe",
            state_version=1,
            decision_id="ev1_01_reception_bag",
            option_id="a_reject_on_site",
            client_action_id="terminal-action-0001",
        )

        self.assertEqual(2, result["state_version"])
        request = captured["request"]
        self.assertEqual(
            "http://example.test/api/game/session/game%2Funsafe/action",
            request.full_url,
        )
        self.assertEqual("acct_terminal", request.get_header("X-account-id"))
        self.assertEqual(3.5, captured["timeout"])
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual("decision", payload["input_mode"])
        self.assertEqual("a_reject_on_site", payload["option_id"])
        self.assertNotIn("integrity", payload)

    def test_structured_backend_error_is_preserved(self) -> None:
        body = json.dumps({
            "error": {
                "code": "DECISION_REQUIRED",
                "message": "必须先处理当前决策",
                "details": {},
            }
        }, ensure_ascii=False).encode("utf-8")

        def opener(request, *, timeout):
            raise HTTPError(request.full_url, 409, "Conflict", {}, BytesIO(body))

        client = ApiClient("http://example.test", "acct_terminal", opener=opener)
        with self.assertRaises(ApiError) as raised:
            client.get_view("game_1")
        self.assertEqual("DECISION_REQUIRED", raised.exception.code)
        self.assertEqual(409, raised.exception.status)
        self.assertEqual("必须先处理当前决策", raised.exception.message)


if __name__ == "__main__":
    unittest.main()
