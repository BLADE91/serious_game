from __future__ import annotations

from collections import Counter
from dataclasses import replace
import argparse
import json
import os
from pathlib import Path
import time

from fastapi.testclient import TestClient

from run_real_v3_routes import RealRouteRunner
from serious_game_backend.config import Settings


def _expect(response, status: int = 200) -> dict:
    if response.status_code != status:
        raise AssertionError(
            f"expected HTTP {status}, received {response.status_code}: {response.text}"
        )
    return response.json()


def _governance_action(
    client: TestClient, session_id: str, headers: dict[str, str], payload: dict
) -> dict:
    return _expect(
        client.post(
            f"/api/game/session/{session_id}/governance/actions",
            headers=headers,
            json=payload,
        ),
        201,
    )


def _finish_action(
    client: TestClient,
    session_id: str,
    headers: dict[str, str],
    action_id: str,
    state_version: int,
) -> dict:
    return _expect(
        client.post(
            f"/api/game/session/{session_id}/governance/actions/{action_id}/finish",
            headers=headers,
            json={"state_version": state_version},
        )
    )


def _audit_summary(container, session_id: str) -> dict:
    audits = container.llm_audits.list_for_session(session_id)
    providers = Counter(item.provider for item in audits)
    statuses = Counter(item.status for item in audits)
    return {
        "calls": len(audits),
        "statuses": dict(statuses),
        "providers": dict(providers),
        "fake_calls": sum(
            count for provider, count in providers.items()
            if "fake" in provider.casefold()
        ),
    }


def _start_and_talk(
    client: TestClient,
    session_id: str,
    headers: dict[str, str],
    *,
    state_version: int,
    action_kind: str,
    target_id: str,
    topic: str,
    player_text: str,
) -> dict:
    action_catalog = _expect(
        client.get(f"/api/game/session/{session_id}/actions", headers=headers)
    )["actions"]
    action_descriptor = next(
        item for item in action_catalog if item["action_id"] == action_kind
    )
    variant = next(
        (
            item
            for item in action_descriptor["variants"]
            if not item.get("target_choices")
            or target_id
            in {choice["target_id"] for choice in item["target_choices"]}
        ),
        action_descriptor["variants"][0],
    )
    started = _governance_action(
        client,
        session_id,
        headers,
        {
            "state_version": state_version,
            "action_kind": action_kind,
            "variant_id": variant["variant_id"],
            "location_id": variant["location_choices"][0]["location_id"],
            "target_ids": [target_id],
            "topic": topic,
        },
    )
    action_id = started["action"]["action_instance_id"]
    turn = _expect(
        client.post(
            f"/api/game/session/{session_id}/governance/actions/{action_id}/turn",
            headers=headers,
            json={
                "state_version": started["state_version"],
                "player_text": player_text,
                "client_action_id": f"feature-turn-{action_id}",
            },
        )
    )
    return {
        "started": started,
        "turn": turn,
        "action_id": action_id,
    }


def _server_default_workflow(
    runner: RealRouteRunner, root: Path
) -> dict:
    container, client, session_id, headers = runner.build_real_runner(0)
    result, decision_index = runner.reach_day_three(
        container, client, session_id, headers, 0
    )
    result, decision_index = runner.drain_decisions(
        container,
        client,
        session_id,
        headers,
        result,
        0,
        decision_index,
    )
    ap_before = result["visible_state"]["ledger"]["action_points"]["remaining"]

    actions = _expect(
        client.get(f"/api/game/session/{session_id}/actions", headers=headers)
    )["actions"]
    archive = next(
        variant
        for action in actions
        for variant in action["variants"]
        if variant["variant_id"] == "consult_county_archives"
    )
    archived = _governance_action(
        client,
        session_id,
        headers,
        {
            "state_version": result["state_version"],
            "action_kind": archive["action_id"],
            "variant_id": archive["variant_id"],
            "location_id": archive["location_choices"][0]["location_id"],
            "archive_ids": [archive["target_choices"][0]["target_id"]],
        },
    )

    household = _start_and_talk(
        client,
        session_id,
        headers,
        state_version=archived["state_version"],
        action_kind="household_visit",
        target_id="npc_zhou_dashan",
        topic="核实搬迁程序与逐户顾虑",
        player_text="请只说明已经确认的搬迁顾虑、程序依据和需要落实的下一步。",
    )
    finished_household = _finish_action(
        client,
        session_id,
        headers,
        household["action_id"],
        household["turn"]["state_version"],
    )

    cadre = _start_and_talk(
        client,
        session_id,
        headers,
        state_version=finished_household["state_version"],
        action_kind="cadre_interview",
        target_id="npc_zheng_xiangdong",
        topic="核实财政边界与公开口径",
        player_text="请按现有文件说明财政边界、公开口径和责任期限。",
    )
    finished_cadre = _finish_action(
        client,
        session_id,
        headers,
        cadre["action_id"],
        cadre["turn"]["state_version"],
    )

    meeting_action = next(
        item for item in actions if item["action_id"] == "leadership_meeting"
    )
    meeting_variant = meeting_action["variants"][0]
    meeting = _governance_action(
        client,
        session_id,
        headers,
        {
            "state_version": finished_cadre["state_version"],
            "action_kind": "leadership_meeting",
            "variant_id": meeting_variant["variant_id"],
            "location_id": meeting_variant["location_choices"][0]["location_id"],
            "target_ids": ["npc_zhao_jianguo", "npc_sun_qiang"],
            "lead_npc_id": "npc_zhao_jianguo",
            "topic": "搬迁材料自查与责任分工",
            "archive_ids": ["archive:doc_compensation_policy_v1"],
            "proposed_document_type": "investigation_notice",
        },
    )
    meeting_id = meeting["meeting"]["meeting_id"]
    meeting_turn = _expect(
        client.post(
            f"/api/game/session/{session_id}/governance/meetings/{meeting_id}/turn",
            headers=headers,
            json={
                "state_version": meeting["state_version"],
                "player_text": "请分别表态，并明确材料自查、责任人、期限和公开范围。",
                "client_action_id": f"feature-meeting-turn-{meeting_id}",
            },
        )
    )
    resolved = _expect(
        client.post(
            f"/api/game/session/{session_id}/governance/meetings/{meeting_id}/resolve",
            headers=headers,
            json={
                "state_version": meeting_turn["state_version"],
                "adopt": True,
                "resolution": {
                    "decision": "启动搬迁材料专项自查并公开办理节点",
                    "target_scope": "搬迁项目现有材料",
                    "resources": {},
                    "resource_mode": "authorization_ceiling",
                    "responsible_ids": ["npc_zhao_jianguo", "npc_sun_qiang"],
                    "deadline_day": 10,
                    "public_scope": ["全村36户"],
                    "document_title": "柳林村搬迁材料专项调查通知",
                },
            },
        )
    )
    document = resolved["document"]
    countersigned = resolved
    countersign_attempts = 0
    while countersign_attempts < 3:
        countersign_attempts += 1
        countersigned = _expect(
            client.post(
                f"/api/game/session/{session_id}/governance/documents/{document['document_id']}/countersign",
                headers=headers,
                json={
                    "state_version": countersigned["state_version"],
                    "npc_id": "npc_zhao_jianguo",
                },
            )
        )
        if countersigned.get("accepted"):
            break
    if not countersigned.get("accepted"):
        raise AssertionError(
            "document countersigner rejected three explicit player attempts"
        )
    issued = _expect(
        client.post(
            f"/api/game/session/{session_id}/governance/documents/{document['document_id']}/issue",
            headers=headers,
            json={"state_version": countersigned["state_version"]},
        )
    )

    saved = _expect(
        client.post(
            f"/api/game/session/{session_id}/manual-saves",
            headers=headers,
            json={
                "client_action_id": "feature-server-manual-save",
                "state_version": issued["state_version"],
                "slot_number": 1,
                "display_name": "真实接口全功能节点",
                "overwrite": False,
            },
        )
    )
    before_load = container.sessions.get_owned(session_id, headers["X-Account-ID"])
    loaded = _expect(
        client.post(
            f"/api/game/session/{session_id}/load-snapshot",
            headers=headers,
            json={
                "client_action_id": "feature-server-manual-load",
                "state_version": saved["state_version"],
                "snapshot_id": saved["snapshot_id"],
                "confirmed": True,
            },
        )
    )
    after_load = container.sessions.get_owned(session_id, headers["X-Account-ID"])
    if before_load is None or after_load is None:
        raise AssertionError("session disappeared during save/load workflow")
    semantic_equal = (
        before_load.game_state.action_points == after_load.game_state.action_points
        and before_load.flags == after_load.flags
        and before_load.completed_conversations == after_load.completed_conversations
        and set(before_load.administrative_documents)
        == set(after_load.administrative_documents)
    )
    if not semantic_equal:
        raise AssertionError("manual load did not restore the saved business state")
    audit = _audit_summary(container, session_id)
    if audit["fake_calls"] or audit["statuses"].get("failed"):
        raise AssertionError(f"real server workflow audit failed: {audit}")
    return {
        "account": "server_default",
        "session_id": session_id,
        "story_day": after_load.game_state.story_day,
        "action_points_before": ap_before,
        "action_points_after": after_load.game_state.action_points,
        "four_action_families": [
            "inspect_archives",
            "household_visit",
            "cadre_interview",
            "leadership_meeting",
        ],
        "meeting_npc_order": [
            item["npc_id"]
            for item in meeting_turn["transcript"]
            if item["speaker_type"] == "npc"
        ],
        "document_status": issued["document"]["status"],
        "countersign_attempts": countersign_attempts,
        "manual_save_load_semantic_equal": semantic_equal,
        "audit": audit,
    }


def _personal_contract_workflow(
    runner: RealRouteRunner, root: Path
) -> dict:
    container, client, session_id, headers = runner.build_real_runner(1)
    result, decision_index = runner.reach_day_three(
        container, client, session_id, headers, 1
    )
    result, decision_index = runner.drain_decisions(
        container,
        client,
        session_id,
        headers,
        result,
        1,
        decision_index,
    )
    conversation = _start_and_talk(
        client,
        session_id,
        headers,
        state_version=result["state_version"],
        action_kind="household_visit",
        target_id="npc_wu_xiuying",
        topic="逐户合同与公开签约",
        player_text=(
            "我明确向你代表的每一户分别发起合同，请逐户签约，并按公开政策逐项核对。"
        ),
    )
    proposal = conversation["turn"].get("contract_batch_proposal")
    if not proposal:
        raise AssertionError("real household conversation did not create contract batch")
    confirmed = _expect(
        client.post(
            f"/api/game/session/{session_id}/governance/contract-batches/{proposal['batch_id']}/confirm",
            headers=headers,
            json={
                "state_version": conversation["turn"]["state_version"],
                "confirmed": True,
            },
        )
    )
    contract = next(
        item for item in confirmed["contracts"] if item["household_id"] == "WU-01"
    )
    terms = _expect(
        client.put(
            f"/api/game/session/{session_id}/governance/contracts/{contract['contract_id']}/terms",
            headers=headers,
            json={
                "state_version": confirmed["state_version"],
                "policy_document_id": "doc_compensation_policy_v1",
                "cash_amount": 50,
                "budget_envelope": "property_land",
                "housing_resource_id": "housing_d1_80",
                "service_allocations": {"grave_relocation_service": 1},
                "payment_day": 5,
                "move_out_day": 20,
                "housing_delivery_day": 20,
                "transition_months": 0,
                "public_window_reward": False,
                "approval_document_ids": [],
                "authorization_confirmed": False,
                "real_unit_viewed": False,
                "ledger_disclosed": False,
                "old_case_resolved": False,
                "prior_payment_verified": False,
            },
        )
    )
    if terms["contract"]["audit_status"] != "pass":
        raise AssertionError(f"contract draft audit failed: {terms['contract']['audit_result']}")
    reviewed = _expect(
        client.post(
            f"/api/game/session/{session_id}/governance/contracts/{contract['contract_id']}/review",
            headers=headers,
            json={"state_version": terms["state_version"]},
        )
    )
    if reviewed["contract"]["status"] != "accepted":
        raise AssertionError(
            f"contract was not accepted: {reviewed['contract']['review_history']}"
        )
    signed = _expect(
        client.post(
            f"/api/game/session/{session_id}/governance/contracts/{contract['contract_id']}/sign",
            headers=headers,
            json={"state_version": reviewed["state_version"], "confirmed": True},
        )
    )
    if not signed["signed"]:
        raise AssertionError("contract sign endpoint did not settle the contract")
    result = _finish_action(
        client,
        session_id,
        headers,
        conversation["action_id"],
        signed["state_version"],
    )
    result = runner.end_day(
        client, session_id, headers, result, "feature-personal-end-d3"
    )
    for day in range(4, 46):
        while result["visible_state"].get("active_group_conversation"):
            group = result["visible_state"]["active_group_conversation"]
            result = _expect(
                client.post(
                    f"/api/game/session/{session_id}/group-conversation/turn",
                    headers=headers,
                    json={
                        "state_version": result["state_version"],
                        "player_text": "请逐项确认责任、依据和期限。",
                        "client_action_id": (
                            f"feature-personal-group-{day}-"
                            f"{group['conversation_id']}-{result['state_version']}"
                        ),
                    },
                )
            )
        result, decision_index = runner.drain_decisions(
            container,
            client,
            session_id,
            headers,
            result,
            1,
            decision_index,
        )
        result = runner.end_day(
            client,
            session_id,
            headers,
            result,
            f"feature-personal-end-d{day}",
        )
    while result["visible_state"].get("active_group_conversation"):
        group = result["visible_state"]["active_group_conversation"]
        result = _expect(
            client.post(
                f"/api/game/session/{session_id}/group-conversation/turn",
                headers=headers,
                json={
                    "state_version": result["state_version"],
                    "player_text": "请逐项确认责任、依据和期限。",
                    "client_action_id": (
                        f"feature-personal-group-46-"
                        f"{group['conversation_id']}-{result['state_version']}"
                    ),
                },
            )
        )
    result, decision_index = runner.drain_decisions(
        container,
        client,
        session_id,
        headers,
        result,
        1,
        decision_index,
    )
    map_document = _expect(
        client.get(f"/api/game/session/{session_id}/map", headers=headers)
    )
    map_records = []
    for location_id in (
        "loc_liulin_village",
        "loc_hongda_factory",
        "loc_county_hospital",
    ):
        location = next(
            item for item in map_document["locations"]
            if item["location_id"] == location_id
        )
        card = next(
            item for item in location["entry_cards"]
            if item["variant_id"] == "field_visit" and item["available"]
        )
        minimum = int(card["participant_rules"]["minimum"])
        selected_targets = list(card["preselected_npc_ids"])
        if len(selected_targets) < minimum:
            selected_targets = [
                item["target_id"] for item in card["target_choices"][:minimum]
            ]
        before = container.sessions.get_owned(session_id, headers["X-Account-ID"])
        if before is None:
            raise AssertionError("map workflow session disappeared")
        started = _governance_action(
            client,
            session_id,
            headers,
            {
                "state_version": before.state_version,
                "action_kind": card["action_id"],
                "variant_id": card["variant_id"],
                "location_id": card["preselected_location_id"],
                "map_entry_id": card["map_entry_id"],
                "target_ids": selected_targets,
                "topic": f"在{location['name']}核实公开事项和办理进度",
            },
        )
        if started["action"]["location_id"] != location_id:
            raise AssertionError("map action did not preserve its locked location")
        if started["visible_state"]["ledger"]["action_points"]["remaining"] != before.game_state.action_points:
            raise AssertionError("map action charged AP before the first successful turn")
        cancelled = _expect(
            client.post(
                f"/api/game/session/{session_id}/governance/actions/"
                f"{started['action']['action_instance_id']}/cancel",
                headers=headers,
                json={"state_version": started["state_version"]},
            )
        )
        map_records.append({
            "location_id": location_id,
            "location_name": location["name"],
            "map_entry_id": card["map_entry_id"],
            "location_locked": card["location_locked"],
            "cancelled_without_cost": (
                cancelled["visible_state"]["ledger"]["action_points"]["remaining"]
                == before.game_state.action_points
            ),
        })
    stored = container.sessions.get_owned(session_id, headers["X-Account-ID"])
    if stored is None:
        raise AssertionError("personal workflow session disappeared")
    audit = _audit_summary(container, session_id)
    if audit["fake_calls"] or audit["statuses"].get("failed"):
        raise AssertionError(f"real personal workflow audit failed: {audit}")
    return {
        "account": "personal",
        "session_id": session_id,
        "story_day": stored.game_state.story_day,
        "contract_batch_households": len(confirmed["contracts"]),
        "signed_contract_id": contract["contract_id"],
        "signed_households": stored.game_state.signed_households,
        "contract_audit_status": terms["contract"]["audit_status"],
        "contract_review_status": reviewed["contract"]["status"],
        "map_locations": map_records,
        "audit": audit,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings.from_env()
    if settings.role_llm_provider != "openai_compatible":
        raise SystemExit("real feature workflow requires openai_compatible")
    if settings.role_llm_fallback_to_fake:
        raise SystemExit("real feature workflow refuses Fake fallback")
    if not os.getenv(settings.role_llm_api_key_env, "").strip():
        raise SystemExit("configured real API key is missing")
    root = args.output_dir / f"workflows-{int(time.time())}"
    root.mkdir(parents=True, exist_ok=False)
    runner = RealRouteRunner(settings, root, stop_day=3)
    started = time.perf_counter()
    workflows = [
        _server_default_workflow(runner, root),
        _personal_contract_workflow(runner, root),
    ]
    report = {
        "provider": "openai_compatible",
        "model": settings.role_llm_model,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "fake_calls": sum(item["audit"]["fake_calls"] for item in workflows),
        "workflows": workflows,
    }
    (root / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
