import json
import unittest

from src.config import PABackendConfig
from src.generation.pa_backend_script_client import PABackendScriptClient


class PABackendScriptClientTests(unittest.TestCase):
    def test_reuses_one_conversation_across_stage_calls(self) -> None:
        class RecordingClient(PABackendScriptClient):
            def __init__(self):
                super().__init__(
                    PABackendConfig(
                        base_url="http://pa.test",
                        supabase_url="http://supabase.test",
                        supabase_key="supabase-key",
                        account="user@test.com",
                        password="password",
                        collection_id="collection-1",
                    )
                )
                self.calls = []

            def _post(self, url, payload, token, extra_headers=None):
                self.calls.append({
                    "url": url,
                    "payload": payload,
                    "token": token,
                    "extra_headers": extra_headers or {},
                })
                if url.endswith("/auth/sso/login-password"):
                    return '{"access_token": "token-1", "user": {"id": "user-1"}}'
                if url.endswith("/rest/v1/conversations?select=id"):
                    return '[{"id": "conversation-1"}]'
                return 'event: content\ndata: "{\\"ok\\": true}"\n\n'

        client = RecordingClient()

        first = client.complete([FakeMessage("system", "阶段一"), FakeMessage("user", "{}")])
        second = client.complete([FakeMessage("system", "阶段二"), FakeMessage("user", "{}")])

        self.assertEqual(first, '{"ok": true}')
        self.assertEqual(second, '{"ok": true}')
        agent_calls = [
            call for call in client.calls
            if call["url"].endswith("/agent/os-search/general")
        ]
        conversation_creates = [
            call for call in client.calls
            if call["url"].endswith("/rest/v1/conversations?select=id")
        ]
        self.assertEqual(len(conversation_creates), 1)
        self.assertEqual(len(agent_calls), 2)
        self.assertEqual(agent_calls[0]["payload"]["conversation_id"], "conversation-1")
        self.assertEqual(agent_calls[1]["payload"]["conversation_id"], "conversation-1")
        self.assertEqual(agent_calls[0]["payload"]["collection_ids"], ["collection-1"])
        self.assertTrue(agent_calls[0]["payload"]["enable_web_search"])

    def test_reads_sse_response_incrementally(self) -> None:
        class FakeResponse:
            def __init__(self, lines):
                self._lines = [line.encode("utf-8") for line in lines]
                self._index = 0

            def readline(self):
                if self._index >= len(self._lines):
                    return b""
                line = self._lines[self._index]
                self._index += 1
                return line

        client = PABackendScriptClient(
            PABackendConfig(
                base_url="http://pa.test",
                supabase_url="http://supabase.test",
                supabase_key="supabase-key",
                account="user@test.com",
                password="password",
            )
        )
        counts = []
        response = FakeResponse([
            "event: content\n",
            'data: "foo"\n',
            "\n",
            "event: content\n",
            'data: "bar"\n',
            "\n",
        ])

        content = client._read_sse_response(response, counts.append)

        self.assertEqual(content, "foobar")
        self.assertEqual(counts, [3, 6])

    def test_reports_non_content_sse_events(self) -> None:
        class FakeResponse:
            def __init__(self, lines):
                self._lines = [line.encode("utf-8") for line in lines]
                self._index = 0

            def readline(self):
                if self._index >= len(self._lines):
                    return b""
                line = self._lines[self._index]
                self._index += 1
                return line

        client = PABackendScriptClient(
            PABackendConfig(
                base_url="http://pa.test",
                supabase_url="http://supabase.test",
                supabase_key="supabase-key",
                account="user@test.com",
                password="password",
            )
        )
        progress = []
        response = FakeResponse([
            "event: thought\n",
            'data: {"stage": "search", "label": "正在检索知识库"}\n',
            "\n",
            "event: tool_call\n",
            'data: {"name": "os_search", "arguments": {"query": "生态搬迁"}}\n',
            "\n",
            "event: tool_result\n",
            'data: {"name": "os_search", "preview": "命中 3 条", "is_error": false}\n',
            "\n",
            "event: usage_update\n",
            'data: {"billing_mode": "general_agent", "current_chain_tokens": 1200}\n',
            "\n",
            "event: content\n",
            'data: "done"\n',
            "\n",
        ])

        content = client._read_sse_response(
            response,
            lambda chars, event=None: progress.append((chars, event)),
        )

        self.assertEqual(content, "done")
        event_names = [event.get("event") for _, event in progress if event]
        self.assertIn("thought", event_names)
        self.assertIn("tool_call", event_names)
        self.assertIn("tool_result", event_names)
        self.assertIn("usage_update", event_names)
        self.assertIn("content", event_names)
        messages = [event.get("message", "") for _, event in progress if event]
        self.assertTrue(any("正在检索知识库" in message for message in messages))
        self.assertTrue(any("os_search" in message for message in messages))

    def test_retries_empty_stage_with_new_conversation(self) -> None:
        class RetryClient(PABackendScriptClient):
            def __init__(self):
                super().__init__(PABackendConfig(
                    base_url="http://pa.test",
                    supabase_url="http://supabase.test",
                    supabase_key="supabase-key",
                    account="user@test.com",
                    password="password",
                    max_stage_retries=2,
                    retry_backoff_seconds=0,
                ))
                self.agent_calls = 0
                self.conversation_creates = 0

            def _post(self, url, payload, token, extra_headers=None):
                if url.endswith("/auth/sso/login-password"):
                    return '{"access_token": "token-1", "user": {"id": "user-1"}}'
                if url.endswith("/rest/v1/conversations?select=id"):
                    self.conversation_creates += 1
                    return json.dumps([{
                        "id": f"conversation-{self.conversation_creates}",
                    }])
                self.agent_calls += 1
                if self.agent_calls < 3:
                    return "event: done\ndata: {}\n\n"
                return 'event: content\ndata: {"content": "recovered"}\n\n'

        client = RetryClient()
        content = client.complete([FakeMessage("user", "retry")])

        self.assertEqual(content, "recovered")
        self.assertEqual(client.agent_calls, 3)
        self.assertEqual(client.conversation_creates, 3)

    def test_empty_stage_error_includes_event_summary(self) -> None:
        class EmptyClient(PABackendScriptClient):
            def __init__(self):
                super().__init__(PABackendConfig(
                    base_url="http://pa.test",
                    supabase_url="http://supabase.test",
                    supabase_key="supabase-key",
                    account="user@test.com",
                    password="password",
                    max_stage_retries=0,
                ))

            def _post(self, url, payload, token, extra_headers=None):
                if url.endswith("/auth/sso/login-password"):
                    return '{"access_token": "token-1", "user": {"id": "user-1"}}'
                if url.endswith("/rest/v1/conversations?select=id"):
                    return '[{"id": "conversation-1"}]'
                return 'event: error\ndata: "backend failed"\n\nevent: done\ndata: {}\n\n'

        with self.assertRaisesRegex(Exception, "error.*done"):
            EmptyClient().complete([FakeMessage("user", "fail")])


class FakeMessage:
    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content


if __name__ == "__main__":
    unittest.main()
