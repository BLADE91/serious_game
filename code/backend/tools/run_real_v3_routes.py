from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from fastapi.testclient import TestClient

from serious_game_backend.api.app import create_app
from serious_game_backend.bootstrap import build_container
from serious_game_backend.config import Settings


def _load_story_route_test_case():
    test_path = Path(__file__).resolve().parents[1] / "tests" / "test_story_routes_v3.py"
    spec = importlib.util.spec_from_file_location("serious_game_story_routes_v3", test_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load route driver: {test_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.StoryRoutesV3Tests


StoryRoutesV3Tests = _load_story_route_test_case()


class RealRouteRunner(StoryRoutesV3Tests):
    def __init__(self, base_settings: Settings, root: Path, *, stop_day: int = 90) -> None:
        super().__init__(methodName="test_three_distinct_fake_routes_reach_d90_without_semantic_leaks")
        self.base_settings = base_settings
        self.root = root
        self.stop_day = stop_day

    def build_real_runner(
        self, route_index: int
    ) -> tuple[object, TestClient, str, dict[str, str]]:
        account_id = f"acct_real_route_{route_index}"
        settings = replace(
            self.base_settings,
            environment="test",
            default_package_id="pkg_gameplay_v3",
            repository="sqlite",
            database_path=self.root / f"route-{route_index}.sqlite",
            auth_required=False,
            allow_self_registration=False,
            role_llm_provider="openai_compatible",
            role_llm_fallback_to_fake=False,
        )
        settings.validate()
        container = build_container(settings)
        if route_index == 1:
            api_key = os.getenv(settings.role_llm_api_key_env, "").strip()
            container.player_llm_configs.use_personal(
                account_id,
                base_url=settings.role_llm_base_url,
                api_key=api_key,
                model=settings.role_llm_model,
            )
            mode = "personal"
        else:
            container.player_llm_configs.use_server_default(account_id)
            mode = "server_default"
        client = TestClient(create_app(settings, container=container))
        headers = {"X-Account-ID": account_id}
        status = client.get("/api/ai/config", headers=headers)
        self.assertEqual(200, status.status_code, status.text)
        self.assertEqual(mode, status.json()["mode"])
        self.assertEqual("compatible", status.json()["compatibility_status"])
        response = client.post(
            "/api/game/session",
            headers=headers,
            json={
                "client_request_id": f"real-story-route-{route_index}",
                "package_id": "pkg_gameplay_v3",
            },
        )
        self.assertEqual(201, response.status_code, response.text)
        return container, client, response.json()["session_id"], headers

    def end_day(self, client, session_id, headers, result: dict, key: str) -> dict:
        print(f"{key}: submitting", file=sys.stderr, flush=True)
        response = client.post(
            f"/api/game/session/{session_id}/end-day",
            headers=headers,
            json={"client_action_id": key, "state_version": result["state_version"]},
        )
        if response.status_code != 200:
            raise AssertionError(
                f"{key}: expected 200, received {response.status_code}: {response.text}"
            )
        return response.json()

    def reach_day_three(
        self, container, client, session_id, headers, route_index: int
    ) -> tuple[dict, int]:
        """Reach D3 through a real conversation that actually satisfies D2."""
        session = container.sessions.get_owned(session_id, headers["X-Account-ID"])
        result = {
            "state_version": session.state_version,
            "visible_state": {
                "pending_decision": {
                    "decision_id": session.pending_decision.decision_id,
                    "input_kind": session.pending_decision.input_kind,
                    "input_schema": session.pending_decision.input_schema,
                    "options": [
                        {"option_id": item.option_id, "available": item.available}
                        for item in session.pending_decision.options
                    ],
                }
            },
        }
        result, decision_index = self.drain_decisions(
            container, client, session_id, headers, result, route_index, 0
        )
        result = self.end_day(
            client, session_id, headers, result, f"real-route-{route_index}-end-d1"
        )
        result, decision_index = self.drain_decisions(
            container, client, session_id, headers, result, route_index, decision_index
        )

        prompts = (
            "请明确说明周氏宗族、散姓和关键村民之间的关系，不要只说笼统顾虑。",
            "请把周氏宗族在村里的权力关系和散姓住户的处境逐项说清楚。",
            "这次接触必须核实村里宗族力量分布，请给出你确认的关系脉络。",
        )
        conversation_id = None
        disclosed = False
        for attempt, player_text in enumerate(prompts, start=1):
            stored = container.sessions.get_owned(session_id, headers["X-Account-ID"])
            if stored.active_conversation is None:
                started = self.action(
                    client,
                    session_id,
                    headers,
                    {
                        "input_mode": "conversation_start",
                        "client_action_id": f"real-route-{route_index}-wu-start-{attempt}",
                        "state_version": result["state_version"],
                        "opportunity_id": "opp_d02_wu_xiuying_first_talk",
                        "target_npc_id": "npc_wu_xiuying",
                    },
                )
                result = started
                conversation_id = started["conversation"]["conversation_id"]
            else:
                conversation_id = stored.active_conversation.conversation_id
            result = self.action(
                client,
                session_id,
                headers,
                {
                    "input_mode": "free_text",
                    "client_action_id": f"real-route-{route_index}-wu-talk-{attempt}",
                    "state_version": result["state_version"],
                    "conversation_id": conversation_id,
                    "opportunity_id": "opp_d02_wu_xiuying_first_talk",
                    "target_npc_id": "npc_wu_xiuying",
                    "player_text": player_text,
                },
            )
            stored = container.sessions.get_owned(session_id, headers["X-Account-ID"])
            disclosed = any(
                item.get("type") == "conversation_turn"
                and item.get("conversation_id") == conversation_id
                and item.get("disclosure_id") == "fact_clan_power_map"
                for item in stored.logs
            )
            if disclosed or "flag_wu_first_talk_completed" in stored.flags:
                break
        self.assertTrue(disclosed, "real D2 conversation did not disclose clan map")
        stored = container.sessions.get_owned(session_id, headers["X-Account-ID"])
        if stored.active_conversation is not None:
            result = self.action(
                client,
                session_id,
                headers,
                {
                    "input_mode": "conversation_end",
                    "client_action_id": f"real-route-{route_index}-wu-end",
                    "state_version": result["state_version"],
                    "conversation_id": stored.active_conversation.conversation_id,
                },
            )
            self.assertEqual("completed", result.get("completion_status"))
        return (
            self.end_day(
                client,
                session_id,
                headers,
                result,
                f"real-route-{route_index}-end-d2",
            ),
            decision_index,
        )

    def run_route(self, route_index: int) -> dict:
        container, client, session_id, headers = self.build_real_runner(route_index)
        started = time.perf_counter()
        result, decision_index = self.reach_day_three(
            container, client, session_id, headers, route_index
        )
        visited_days = [3]
        group_records: list[dict] = []
        for story_day in range(3, 91):
            if result["visible_state"]["status"] == "ended":
                break
            while result["visible_state"].get("active_group_conversation"):
                group = dict(result["visible_state"]["active_group_conversation"])
                response = client.post(
                    f"/api/game/session/{session_id}/group-conversation/turn",
                    headers=headers,
                    json={
                        "state_version": result["state_version"],
                        "player_text": "请各位逐项说明已经确认的责任、依据和完成期限。",
                        "client_action_id": (
                            f"real-route-{route_index}-group-{story_day:02d}-"
                            f"{len(group_records) + 1:02d}"
                        ),
                    },
                )
                self.assertEqual(200, response.status_code, response.text)
                result = response.json()
                group_records.append({
                    "story_day": story_day,
                    "conversation_id": group.get("conversation_id"),
                    "conversation_type": group.get("conversation_type"),
                    "participant_ids": group.get("participant_ids", []),
                    "agenda": group.get("agenda"),
                    "round_after": (
                        result["visible_state"].get("active_group_conversation") or {}
                    ).get("round_index"),
                })
            result, decision_index = self.drain_decisions(
                container,
                client,
                session_id,
                headers,
                result,
                route_index,
                decision_index,
            )
            if result["visible_state"]["story"]["day"] >= self.stop_day:
                break
            result = self.end_day(
                client,
                session_id,
                headers,
                result,
                f"real-route-{route_index}-end-{story_day:02d}",
            )
            visited_days.append(result["visible_state"]["story"]["day"])

        if self.stop_day == 90:
            self.assertEqual("ended", result["visible_state"]["status"])
            self.assertEqual(90, result["visible_state"]["story"]["day"])
        else:
            self.assertGreaterEqual(
                result["visible_state"]["story"]["day"], self.stop_day
            )
        stored = container.sessions.get_owned(session_id, headers["X-Account-ID"])
        audits = container.llm_audits.list_for_session(session_id)
        statuses = Counter(item.status for item in audits)
        providers = Counter(item.provider for item in audits)
        night_plans = [
            {
                "story_day": record["story_day"],
                "created_plan_ids": [
                    item.get("plan_id")
                    for item in record.get("followup_decisions", ())
                    if item.get("created")
                ],
                "agent_exchange_count": len(record.get("agent_exchanges", ())),
            }
            for record in stored.night_logs
            if record.get("followup_decisions")
        ]
        return {
            "route_index": route_index,
            "session_id": session_id,
            "mode": "personal" if route_index == 1 else "server_default",
            "story_day": stored.game_state.story_day,
            "status": stored.status.value,
            "ending_id": (stored.ending_result or {}).get("main_ending_id"),
            "visited_days": len(visited_days),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "llm_audits": len(audits),
            "audit_statuses": dict(statuses),
            "providers": dict(providers),
            "fake_calls": sum(
                count for provider, count in providers.items()
                if "fake" in provider.casefold()
            ),
            "group_turns": group_records,
            "night_followups": night_plans,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--routes", type=int, default=3, choices=(1, 2, 3))
    parser.add_argument("--start-index", type=int, default=0, choices=(0, 1, 2))
    parser.add_argument("--stop-day", type=int, default=90, choices=range(3, 91))
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    if args.start_index + args.routes > 3:
        parser.error("start-index + routes must not exceed 3")
    base_settings = Settings.from_env()
    if base_settings.role_llm_provider != "openai_compatible":
        raise SystemExit("real route acceptance requires openai_compatible")
    if base_settings.role_llm_fallback_to_fake:
        raise SystemExit("real route acceptance refuses Fake fallback")
    if not os.getenv(base_settings.role_llm_api_key_env, "").strip():
        raise SystemExit("configured real API key is missing")
    temporary = None
    if args.output_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="serious-game-real-routes-")
        root = Path(temporary.name)
    else:
        root = args.output_dir / f"run-{int(time.time())}"
        root.mkdir(parents=True, exist_ok=False)
        print(f"evidence_dir={root}", file=sys.stderr, flush=True)
    try:
        runner = RealRouteRunner(base_settings, root, stop_day=args.stop_day)
        routes = [
            runner.run_route(index)
            for index in range(args.start_index, args.start_index + args.routes)
        ]
    finally:
        if temporary is not None:
            temporary.cleanup()
    report = {
        "provider": "openai_compatible",
        "model": base_settings.role_llm_model,
        "fake_calls": sum(item["fake_calls"] for item in routes),
        "routes": routes,
    }
    if args.output_dir is not None:
        (root / "summary.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(
        item["story_day"] >= args.stop_day
        and item["fake_calls"] == 0
        and (args.stop_day < 90 or item["status"] == "ended")
        for item in routes
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
