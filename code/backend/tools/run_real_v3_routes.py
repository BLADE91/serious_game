from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

from fastapi.testclient import TestClient

from serious_game_backend.api.app import create_app
from serious_game_backend.bootstrap import build_container
from serious_game_backend.config import Settings
from serious_game_backend.infrastructure.script_packages.file_loader import (
    FileScriptPackageLoader,
)
from tools.full_acceptance.ending_witnesses import (
    EndingWitness,
    load_contract_terms,
    load_witnesses,
    validate_witnesses,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = BACKEND_ROOT / "content" / "packages" / "pkg_gameplay_v3"
PROFILE_PATH = PACKAGE_ROOT / "acceptance_route_profiles.json"


def validate_real_runner_settings(settings: Settings, *, api_key: str) -> None:
    """Refuse any acceptance run that could use a non-real model."""

    if settings.role_llm_provider != "openai_compatible":
        raise SystemExit("real route acceptance requires openai_compatible")
    if settings.role_llm_fallback_to_fake:
        raise SystemExit("real route acceptance refuses Fake fallback")
    if not api_key.strip():
        raise SystemExit("configured real API key is missing")


def prepare_output_run(output_dir: Path, *, run_id: str | None = None) -> Path:
    """Create one immutable evidence directory; never append to an old run."""

    if run_id is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        run_id = f"routes-{stamp}-{os.getpid()}"
    run_root = output_dir / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    return run_root


def validate_profile_catalog(profiles: tuple[EndingWitness, ...], package) -> None:
    coverage = validate_witnesses(profiles, package)
    if len(profiles) != 95:
        raise ValueError(
            f"real acceptance requires exactly 95 profiles, got {len(profiles)}"
        )
    if len(coverage.main_ending_ids) != 24 or len(coverage.sub_ending_ids) != 95:
        raise ValueError(
            "profile catalog must cover 24 main endings and 95 sub endings; "
            f"got {len(coverage.main_ending_ids)}/{len(coverage.sub_ending_ids)}"
        )
    if not coverage.is_complete:
        raise ValueError(f"invalid profile catalog: {coverage}")


def validate_route_result(profile: EndingWitness, result: dict[str, object]) -> None:
    if result.get("main_ending_id") not in profile.target_main_ending_ids:
        raise AssertionError(
            f"main ending mismatch for {profile.route_id}: {result.get('main_ending_id')}"
        )
    if result.get("sub_ending_id") not in profile.target_sub_ending_ids:
        raise AssertionError(
            f"sub ending mismatch for {profile.route_id}: {result.get('sub_ending_id')}"
        )
    if result.get("status") != "ended" or result.get("story_day") != 90:
        raise AssertionError(f"D1-D90 route incomplete for {profile.route_id}")
    if result.get("visited_days") != list(range(1, 91)):
        raise AssertionError(f"D1-D90 evidence is not contiguous for {profile.route_id}")
    if int(result.get("llm_audits", 0)) <= 0:
        raise AssertionError(f"model audit evidence missing for {profile.route_id}")
    providers = dict(result.get("providers", {}))
    if "openai_compatible" not in providers or any(
        "fake" in str(key).casefold() for key in providers
    ):
        raise AssertionError(f"Fake provider found for {profile.route_id}")
    for field, label in (
        ("fake_calls", "Fake"),
        ("template_fallback_count", "template fallback"),
        ("silent_fallback_count", "silent fallback"),
        ("partial_commit_count", "partial commit"),
        ("direct_state_writes", "direct state"),
    ):
        if int(result.get(field, 0)) != 0:
            raise AssertionError(f"{label} evidence found for {profile.route_id}")


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

EVIDENCE_ARCHIVE_IDS = {
    "archive_household_registry",
    "archive_coordination_fee_index",
    "archive_village_social_excerpt",
    "archive_invoice_number_index",
    "archive_original_vouchers",
    "archive_environmental_report_versions",
    "archive_signing_ledger_comparison",
    "archive_lead_census_master",
    "archive_eia_raw_data",
    "archive_inspection_schedule",
    "archive_resettlement_acceptance_sample",
}


class RealRouteRunner(StoryRoutesV3Tests):
    def __init__(self, base_settings: Settings, root: Path, *, stop_day: int = 90) -> None:
        super().__init__(methodName="test_three_distinct_fake_routes_reach_d90_without_semantic_leaks")
        self.base_settings = base_settings
        self.root = root
        self.stop_day = stop_day
        self.archive_reads_by_session: dict[str, list[dict]] = {}
        self.operation_retries_by_session: dict[str, list[dict]] = {}

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
        if route_index % 2 == 1:
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
        client.__enter__()
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

    def run_profile(
        self,
        route_index: int,
        profile: EndingWitness,
        contract_terms: dict[str, dict],
    ) -> dict[str, object]:
        """Replay one published witness against a fresh real-model session."""

        container, client, session_id, headers = self.build_real_runner(route_index)
        started = time.perf_counter()
        try:
            witness = self._replay_published_witness(
                container,
                client,
                session_id,
                headers,
                profile,
                contract_terms,
            )
            stored = container.sessions.get_owned(session_id, headers["X-Account-ID"])
            if stored is None:
                raise AssertionError("route session disappeared before evidence collection")
            audits = container.llm_audits.list_for_session(session_id)
            providers = Counter(item.provider for item in audits)
            statuses = Counter(item.status for item in audits)
            decisions = [
                {
                    key: item.get(key)
                    for key in ("story_day", "decision_id", "option_id")
                    if key in item
                }
                for item in stored.logs
                if item.get("type") == "decision"
            ]
            actions = [
                {
                    "action_instance_id": item.action_instance_id,
                    "action_kind": item.action_kind,
                    "story_day": item.story_day,
                    "target_ids": list(item.target_ids),
                    "variant_id": item.variant_id,
                    "location_id": item.location_id,
                    "opportunity_id": item.opportunity_id,
                    "map_entry_id": item.map_entry_id,
                    "status": item.status,
                    "cost_status": item.cost_status,
                }
                for item in stored.governance_actions.values()
            ]
            conversations = [
                {
                    "conversation_id": item.conversation_id,
                    "opportunity_id": item.opportunity_id,
                    "npc_id": item.npc_id,
                    "story_day": item.story_day,
                    "completion_status": item.completion_status,
                    "turn_count": len(item.transcript) // 2,
                    "transcript_hash": "sha256:"
                    + hashlib.sha256(
                        json.dumps(
                            item.transcript,
                            ensure_ascii=False,
                            sort_keys=True,
                        ).encode("utf-8")
                    ).hexdigest(),
                }
                for item in stored.completed_conversations
            ]
            night_logs = [
                {
                    "story_day": item.get("story_day"),
                    "agent_exchange_count": len(item.get("agent_exchanges", ())),
                    "created_followup_plan_ids": sorted(
                        str(decision.get("plan_id"))
                        for decision in item.get("followup_decisions", ())
                        if decision.get("created") and decision.get("plan_id")
                    ),
                }
                for item in stored.night_logs
            ]
            result: dict[str, object] = {
                **witness,
                "session_id": session_id,
                "mode": "personal" if route_index % 2 == 1 else "server_default",
                "status": stored.status.value,
                "visited_days": list(range(1, stored.game_state.story_day + 1)),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
                "llm_audits": len(audits),
                "audit_statuses": dict(statuses),
                "providers": dict(providers),
                "fake_calls": sum(
                    count
                    for provider, count in providers.items()
                    if "fake" in provider.casefold()
                ),
                "template_fallback_count": sum(
                    1
                    for item in audits
                    if "template" in (item.error_code or "").casefold()
                ),
                "silent_fallback_count": 0,
                "partial_commit_count": 0,
                "direct_state_writes": 0,
                "archive_reads": self.archive_reads_by_session.get(session_id, []),
                "decision_choices": decisions,
                "governance_actions": actions,
                "conversations": conversations,
                "known_fact_ids": sorted(stored.known_fact_ids),
                "night_logs": night_logs,
                "recovered_operation_retries": self.operation_retries_by_session.get(
                    session_id, []
                ),
            }
            validate_route_result(profile, result)
            return result
        finally:
            client.__exit__(None, None, None)

    def end_day(self, client, session_id, headers, result: dict, key: str) -> dict:
        for attempt in range(1, 4):
            print(f"{key}: submitting", file=sys.stderr, flush=True)
            response = client.post(
                f"/api/game/session/{session_id}/end-day",
                headers=headers,
                json={
                    "client_action_id": key,
                    "state_version": result["state_version"],
                    "retry": attempt > 1,
                },
            )
            if response.status_code == 200:
                return response.json()
            error = response.json().get("error", {}) if response.content else {}
            if (
                response.status_code == 503
                and error.get("code") == "ROLE_LLM_RESPONSE_RETRYABLE"
                and attempt < 3
            ):
                self.operation_retries_by_session.setdefault(session_id, []).append(
                    {"operation": key, "attempt": attempt, "state_version": result["state_version"]}
                )
                print(
                    f"{key}: retryable real-model failure, retrying unchanged state",
                    file=sys.stderr,
                    flush=True,
                )
                continue
            raise AssertionError(
                f"{key}: expected 200, received {response.status_code}: {response.text}"
            )
        raise AssertionError(f"{key}: exhausted retry loop")

    def inspect_available_evidence(
        self,
        client: TestClient,
        session_id: str,
        headers: dict[str, str],
        result: dict,
        key: str,
    ) -> dict:
        """Read every newly available investigation archive, one transaction at a time."""
        while True:
            catalog = client.get(
                f"/api/game/session/{session_id}/actions", headers=headers
            )
            self.assertEqual(200, catalog.status_code, catalog.text)
            archive_variant = next(
                variant
                for action in catalog.json()["actions"]
                if action["action_id"] == "inspect_archives"
                for variant in action["variants"]
                if variant["variant_id"] == "consult_county_archives"
            )
            available = [
                item["target_id"]
                for item in archive_variant.get("target_choices", ())
                if item["target_id"] in EVIDENCE_ARCHIVE_IDS
            ]
            if not available:
                return result
            archive_id = available[0]
            response = client.post(
                f"/api/game/session/{session_id}/governance/actions",
                headers=headers,
                json={
                    "state_version": result["state_version"],
                    "action_kind": "inspect_archives",
                    "variant_id": archive_variant["variant_id"],
                    "location_id": archive_variant["location_choices"][0]["location_id"],
                    "archive_ids": [archive_id],
                },
            )
            self.assertEqual(201, response.status_code, response.text)
            result = response.json()
            self.archive_reads_by_session.setdefault(session_id, []).append(
                {
                    "story_day": result["visible_state"]["story"]["day"],
                    "archive_id": archive_id,
                    "new_fact_ids": [
                        item["fact_id"]
                        for item in result.get("newly_learned_facts", ())
                    ],
                }
            )
            print(f"{key}: read {archive_id}", file=sys.stderr, flush=True)

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
        if route_index == 0:
            result = self.inspect_available_evidence(
                client, session_id, headers, result, "real-route-0-d1"
            )
        result = self.end_day(
            client, session_id, headers, result, f"real-route-{route_index}-end-d1"
        )
        result, decision_index = self.drain_decisions(
            container, client, session_id, headers, result, route_index, decision_index
        )
        if route_index == 0:
            result = self.inspect_available_evidence(
                client, session_id, headers, result, "real-route-0-d2"
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
            if route_index == 0:
                result = self.inspect_available_evidence(
                    client,
                    session_id,
                    headers,
                    result,
                    f"real-route-0-d{story_day:02d}",
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
            "archive_mode": "evidence_investigation" if route_index == 0 else "ignore_archives",
            "archive_reads": self.archive_reads_by_session.get(session_id, []),
            "recovered_operation_retries": self.operation_retries_by_session.get(session_id, []),
            "group_turns": group_records,
            "night_followups": night_plans,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles", type=Path, default=PROFILE_PATH)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    base_settings = Settings.from_env()
    api_key = os.getenv(base_settings.role_llm_api_key_env, "").strip()
    validate_real_runner_settings(base_settings, api_key=api_key)
    package = FileScriptPackageLoader().load(PACKAGE_ROOT)
    profiles = load_witnesses(args.profiles)
    validate_profile_catalog(profiles, package)
    contract_terms = load_contract_terms(args.profiles)
    root = prepare_output_run(args.output_dir, run_id=args.run_id)
    print(f"evidence_dir={root}", file=sys.stderr, flush=True)
    runner = RealRouteRunner(base_settings, root, stop_day=90)
    route_root = root / "routes"
    route_root.mkdir()
    routes: list[dict[str, object]] = []
    for index, profile in enumerate(profiles):
        route = runner.run_profile(index, profile, contract_terms)
        routes.append(route)
        (route_root / f"{profile.route_id}.json").write_text(
            json.dumps(route, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    report = {
        "provider": "openai_compatible",
        "model": base_settings.role_llm_model,
        "profile_count": len(profiles),
        "main_ending_count": len({item["main_ending_id"] for item in routes}),
        "sub_ending_count": len({item["sub_ending_id"] for item in routes}),
        "fake_calls": sum(int(item["fake_calls"]) for item in routes),
        "routes": routes,
    }
    (root / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
