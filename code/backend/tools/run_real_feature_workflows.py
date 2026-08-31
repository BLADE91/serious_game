from __future__ import annotations

from collections import Counter
from dataclasses import fields, replace
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from fastapi.testclient import TestClient

from tools.run_real_v3_routes import (
    RealRouteRunner,
    credible_group_replies,
)
from tools.run_real_v3_routes import PROFILE_PATH
from tools.full_acceptance.ending_witnesses import load_contract_terms, load_witnesses
from serious_game_backend.config import Settings
from serious_game_backend.domain.script_package import ScriptPackage
from serious_game_backend.domain.llm_runtime import LLMCallAudit
from serious_game_backend.infrastructure.script_packages.file_loader import (
    FileScriptPackageLoader,
)


EXPECTED_GOVERNANCE_FAMILIES = {
    "inspect_archives",
    "household_visit",
    "cadre_interview",
    "leadership_meeting",
}
PACKAGE_DIR = BACKEND_ROOT / "content" / "packages" / "pkg_gameplay_v3"
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repository, check=True, capture_output=True, text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def capture_run_provenance(repository: Path) -> dict[str, object]:
    """Bind a run to clean tracked bytes and the published v3 manifest."""

    commit = _git(repository, "rev-parse", "HEAD")
    tracked_changes = _git(
        repository, "status", "--porcelain", "--untracked-files=no",
    ).splitlines()
    if tracked_changes:
        raise RuntimeError(
            "real feature evidence refuses a dirty tracked workspace: "
            + ", ".join(line[:200] for line in tracked_changes)
        )
    digest = hashlib.sha256()
    for relative in _git(repository, "ls-files").splitlines():
        path = repository / relative
        if not path.is_file():
            continue
        digest.update(relative.replace("\\", "/").encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    manifest = json.loads(
        (PACKAGE_DIR / "package_manifest.json").read_text(encoding="utf-8")
    )
    declared = str(manifest.get("content_hash", ""))
    computed = FileScriptPackageLoader.compute_content_hash(PACKAGE_DIR)
    if declared != computed:
        raise RuntimeError(
            f"published v3 content hash mismatch: manifest={declared}, computed={computed}"
        )
    return {
        "git_commit": commit,
        "tracked_workspace_clean": True,
        "workspace_fingerprint": digest.hexdigest(),
        "v3_manifest_hash": declared,
        "v3_computed_hash": computed,
    }


def semantic_hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=lambda item: getattr(item, "__dict__", repr(item)),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def assert_required_api_evidence_capabilities() -> None:
    """Stop instead of synthesizing per-call gateway provenance."""

    available = {item.name for item in fields(LLMCallAudit)}
    required = {"endpoint_host", "config_version"}
    missing = sorted(required - available)
    if missing:
        raise RuntimeError(
            "final real feature evidence gap: LLMCallAudit does not expose per-call "
            + " and ".join(missing)
            + "; refusing to infer or fabricate gateway isolation evidence"
        )


def _with_recovery_decision_policy(profile):
    """Keep exploratory routes playable when they unlock mistake-recovery scenes."""

    return replace(
        profile,
        decision_policy={
            **profile.decision_policy,
            "dp5_04_recovery": "a",
            "dp5_05_recovery": "b",
        },
    )


def validate_feature_workflow_report(report: dict[str, object]) -> None:
    """Fail closed unless every published system has real execution evidence."""

    if report.get("provider") != "openai_compatible":
        raise AssertionError("feature workflow did not use openai_compatible")
    for field in (
        "fake_calls", "template_fallback_count", "silent_fallback_count",
        "partial_commit_count",
    ):
        if field not in report or int(report[field]) != 0:
            raise AssertionError(f"{field} must be present and equal zero")
    provenance = report.get("provenance")
    if not isinstance(provenance, dict):
        raise AssertionError("provenance is missing")
    if not _GIT_SHA.fullmatch(str(provenance.get("git_commit", ""))):
        raise AssertionError("provenance git_commit is not a full Git SHA")
    if provenance.get("tracked_workspace_clean") is not True:
        raise AssertionError("tracked workspace was not clean")
    if not re.fullmatch(
        r"[0-9a-f]{64}", str(provenance.get("workspace_fingerprint", ""))
    ):
        raise AssertionError("workspace fingerprint is missing or malformed")
    declared = str(provenance.get("v3_manifest_hash", ""))
    computed = str(provenance.get("v3_computed_hash", ""))
    if not _SHA256.fullmatch(declared) or declared != computed:
        raise AssertionError("v3 content hash does not match the published manifest")
    if int(report.get("server_default_accounts", 0)) < 1:
        raise AssertionError("server-default account evidence is missing")
    if int(report.get("personal_api_accounts", 0)) < 1:
        raise AssertionError("personal API account evidence is missing")
    if report.get("account_gateway_isolation") is not True:
        raise AssertionError("account gateway isolation was not demonstrated")
    requirements = (
        ("archive_ids", 11, "11 archives"),
        ("fact_acquisition_path_ids", 27, "27 fact acquisition paths"),
        ("opportunity_ids", 32, "32 opportunities"),
        ("npc_ids", 29, "29 NPCs"),
        ("map_location_ids", 8, "8 map locations"),
        ("household_ids", 36, "36 households"),
    )
    for field, expected, label in requirements:
        values = {str(item) for item in report.get(field, ())}
        if len(values) != expected:
            raise AssertionError(f"expected {label}, got {len(values)}")
    records = report.get("operation_records")
    if not isinstance(records, dict):
        raise AssertionError("operation_records are missing")
    record_requirements = {
        "archives": 11, "fact_acquisition_paths": 27, "opportunities": 32,
        "npcs": 29, "map_locations": 8, "households": 36,
    }
    for category, expected in record_requirements.items():
        items = records.get(category)
        if not isinstance(items, list) or len(items) != expected:
            raise AssertionError(
                f"expected {expected} {category} operation records"
            )
        evidence_ids = set()
        for item in items:
            required = {
                "evidence_id", "operation_id", "before_version",
                "after_version", "status",
            }
            if not isinstance(item, dict) or not required <= item.keys():
                raise AssertionError(f"{category} operation record is incomplete")
            if int(item["after_version"]) < int(item["before_version"]):
                raise AssertionError(f"{category} operation version regressed")
            evidence_ids.add(str(item["evidence_id"]))
        if len(evidence_ids) != expected:
            raise AssertionError(f"{category} operation records are not unique")
    meeting_record = report.get("meeting_document_record")
    if not isinstance(meeting_record, dict):
        raise AssertionError("meeting/document operation record is missing")
    if meeting_record.get("source_meeting_id") != meeting_record.get("meeting_id"):
        raise AssertionError("document source_meeting_id does not match its meeting")
    if not meeting_record.get("resolution_snapshot"):
        raise AssertionError("document resolution_snapshot is missing")
    if meeting_record.get("document_status") != "issued":
        raise AssertionError("meeting document was not issued")
    steps = meeting_record.get("steps")
    expected_steps = ("meeting", "turn", "resolve", "countersign", "issue")
    if not isinstance(steps, list) or tuple(
        item.get("name") for item in steps if isinstance(item, dict)
    ) != expected_steps:
        raise AssertionError("meeting/document step records are incomplete")
    for item in steps:
        if not item.get("operation_id") or not {
            "before_version", "after_version",
        } <= item.keys():
            raise AssertionError("meeting/document operation identity is missing")
    if not meeting_record.get("llm_audit_ids"):
        raise AssertionError("meeting LLM audit IDs are missing")
    gateway = report.get("gateway_audit")
    gateway_records = gateway.get("records") if isinstance(gateway, dict) else None
    if not isinstance(gateway_records, list) or gateway.get("interleaved") is not True:
        raise AssertionError("account gateway interleaving evidence is missing")
    modes = {item.get("mode") for item in gateway_records if isinstance(item, dict)}
    accounts = {item.get("account_id") for item in gateway_records if isinstance(item, dict)}
    sessions = {item.get("session_id") for item in gateway_records if isinstance(item, dict)}
    required_gateway = {
        "account_id", "session_id", "mode", "endpoint_host", "model",
        "config_version",
    }
    if (
        modes != {"server_default", "personal"}
        or len(accounts) != 2 or len(sessions) != 2
        or any(not required_gateway <= item.keys() for item in gateway_records)
        or any("key" in str(item).casefold() for item in gateway_records)
    ):
        raise AssertionError("account gateway isolation evidence is inconsistent")
    save_load = report.get("save_load_record")
    if not isinstance(save_load, dict) or not {
        "before_semantic_hash", "after_semantic_hash", "save_operation_id",
        "load_operation_id",
    } <= save_load.keys():
        raise AssertionError("save/load semantic record is missing")
    if (
        save_load["before_semantic_hash"] != save_load["after_semantic_hash"]
        or not _SHA256.fullmatch(str(save_load["before_semantic_hash"]))
    ):
        raise AssertionError("save/load semantic hashes do not match")
    families = {str(item) for item in report.get("governance_action_families", ())}
    if families != EXPECTED_GOVERNANCE_FAMILIES:
        raise AssertionError(
            "four governance action families were not all exercised: "
            f"{sorted(families)}"
        )
    for field, label in (
        ("meeting_completed", "meeting"),
        ("contract_completed", "contract"),
        ("document_completed", "document"),
        ("save_load_completed", "save/load"),
        ("review_completed", "review"),
    ):
        if report.get(field) is not True:
            raise AssertionError(f"{label} workflow evidence is missing")
    review_statuses = {
        str(item) for item in report.get("contract_review_statuses", ())
    }
    if "signed" not in review_statuses or "accepted" in review_statuses:
        raise AssertionError(
            "contract review must finish in signed state without a second sign step"
        )


def collect_session_coverage(
    session,
    package: ScriptPackage,
    *,
    map_location_ids: set[str] | None = None,
) -> dict[str, list[str]]:
    """Derive coverage only from persisted, player-reachable transaction records."""

    archive_ids = {
        archive_id
        for archive_id, record in session.archive_records.items()
        if record.read_at_days
        and archive_id
        in {item.archive_id for item in package.archive_investigations}
    }
    completed = {
        item.opportunity_id
        for item in session.completed_conversations
        if item.completion_status == "completed"
    }
    opportunity_ids = {
        item.opportunity_id
        for item in package.interaction_opportunities
        if item.opportunity_id in completed
    }
    npc_ids = {
        item.npc_id
        for item in session.completed_conversations
        if item.completion_status == "completed"
    }
    path_ids: set[str] = set()
    for fact_id, fact in package.facts.items():
        for method in fact.acquisition_methods:
            route_type = str(method["route_type"])
            source_id = str(method["source_id"])
            if (
                route_type == "archive" and source_id in archive_ids
            ) or (
                route_type == "conversation" and source_id in opportunity_ids
            ):
                path_ids.add(f"{fact_id}:{route_type}:{source_id}")
    household_ids = {
        contract.household_id for contract in session.household_contracts.values()
    }
    action_families = {
        item.action_kind for item in session.governance_actions.values()
    }
    return {
        "archive_ids": sorted(archive_ids),
        "fact_acquisition_path_ids": sorted(path_ids),
        "opportunity_ids": sorted(opportunity_ids),
        "npc_ids": sorted(npc_ids),
        "map_location_ids": sorted(map_location_ids or set()),
        "household_ids": sorted(household_ids),
        "governance_action_families": sorted(action_families),
    }


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


def _drain_group_conversations(
    client: TestClient,
    session_id: str,
    headers: dict[str, str],
    result: dict,
    key: str,
    runner: RealRouteRunner | None = None,
) -> dict:
    turn = 0
    while result["visible_state"].get("active_group_conversation"):
        turn += 1
        if turn > 40:
            raise AssertionError("forced conversation did not settle after 40 real turns")
        group = result["visible_state"]["active_group_conversation"]
        resolved = group.get("phase") == "resolved"
        payload = {
            "state_version": result["state_version"],
            "client_action_id": f"{key}-{turn:02d}",
        }
        if resolved:
            response = client.post(
                f"/api/game/session/{session_id}/group-conversation/finish",
                headers=headers,
                json=payload,
            )
        else:
            replies = credible_group_replies(group)
            payload["player_text"] = replies[(turn - 1) % len(replies)]
            if runner is None:
                response = client.post(
                    f"/api/game/session/{session_id}/group-conversation/turn",
                    headers=headers,
                    json=payload,
                )
            else:
                response = runner.group_conversation_turn_for_route(
                    client,
                    session_id,
                    headers,
                    payload,
                )
        result = _expect(response)
    return result


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
        "template_fallback_count": sum(
            "template" in str(item.error_code or "").casefold() for item in audits
        ),
        "silent_fallback_count": sum(
            item.provider != "openai_compatible" for item in audits
        ),
        "records": [{
            "audit_id": item.audit_id,
            "operation_id": item.operation_id,
            "account_id": item.account_id,
            "session_id": item.session_id,
            "provider": item.provider,
            "model": item.model_id,
            "endpoint_host": item.endpoint_host,
            "config_version": item.config_version,
            "status": item.status,
            "error_code": item.error_code,
            "timestamp": item.created_at,
        } for item in audits],
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
    archived = result
    for archive_id in ("archive:doc_compensation_policy_v1",):
        archived = _governance_action(
            client,
            session_id,
            headers,
            {
                "state_version": archived["state_version"],
                "action_kind": archive["action_id"],
                "variant_id": archive["variant_id"],
                "location_id": archive["location_choices"][0]["location_id"],
                "archive_ids": [archive_id],
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
    recovered_retries = len(runner.operation_retries_by_session.get(session_id, ()))
    if (
        audit["fake_calls"]
        or any(
            item.get("state_restored") is not True
            for item in runner.operation_retries_by_session.get(session_id, ())
        )
    ):
        raise AssertionError(f"real server workflow audit failed: {audit}")
    package = container.packages.get("pkg_gameplay_v3")
    if package is None:
        raise AssertionError("v3 package disappeared during server workflow")
    coverage = collect_session_coverage(after_load, package)
    payload = {
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
        "recovered_operation_retries": recovered_retries,
        "audit": audit,
        "coverage": coverage,
    }
    client.__exit__(None, None, None)
    return payload


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
    if reviewed["contract"]["status"] != "signed":
        raise AssertionError(
            "accepted review did not immediately sign the contract: "
            f"{reviewed['contract']['review_history']}"
        )
    result = _finish_action(
        client,
        session_id,
        headers,
        conversation["action_id"],
        reviewed["state_version"],
    )
    result = runner.end_day(
        client, session_id, headers, result, "feature-personal-end-d3"
    )
    for day in range(4, 46):
        result = _drain_group_conversations(
            client, session_id, headers, result,
            f"feature-personal-group-{day}",
            runner,
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
    result = _drain_group_conversations(
        client, session_id, headers, result, "feature-personal-group-46", runner
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
    recovered_retries = len(runner.operation_retries_by_session.get(session_id, ()))
    if (
        audit["fake_calls"]
        or any(
            item.get("state_restored") is not True
            for item in runner.operation_retries_by_session.get(session_id, ())
        )
    ):
        raise AssertionError(f"real personal workflow audit failed: {audit}")
    package = container.packages.get("pkg_gameplay_v3")
    if package is None:
        raise AssertionError("v3 package disappeared during personal workflow")
    coverage = collect_session_coverage(
        stored,
        package,
        map_location_ids={item["location_id"] for item in map_records},
    )
    payload = {
        "account": "personal",
        "session_id": session_id,
        "story_day": stored.game_state.story_day,
        "contract_batch_households": len(confirmed["contracts"]),
        "signed_contract_id": contract["contract_id"],
        "signed_households": stored.game_state.signed_households,
        "contract_audit_status": terms["contract"]["audit_status"],
        "contract_review_status": reviewed["contract"]["status"],
        "map_locations": map_records,
        "recovered_operation_retries": recovered_retries,
        "audit": audit,
        "coverage": coverage,
    }
    client.__exit__(None, None, None)
    return payload


def _exercise_all_available_map_locations(
    client: TestClient,
    container,
    session_id: str,
    headers: dict[str, str],
    result: dict,
    covered: set[str],
) -> dict:
    document = _expect(client.get(f"/api/game/session/{session_id}/map", headers=headers))
    for location in document["locations"]:
        location_id = location["location_id"]
        if location_id in covered:
            continue
        card = next(
            (item for item in location["entry_cards"] if item.get("available")),
            None,
        )
        if card is None:
            continue
        minimum = int(card["participant_rules"]["minimum"])
        selected = list(card.get("preselected_npc_ids", ()))
        if len(selected) < minimum:
            selected = [
                item["target_id"] for item in card.get("target_choices", ())[:minimum]
            ]
        before = container.sessions.get_owned(session_id, headers["X-Account-ID"])
        if before is None:
            raise AssertionError("map inventory session disappeared")
        started = _governance_action(
            client,
            session_id,
            headers,
            {
                "state_version": result["state_version"],
                "action_kind": card["action_id"],
                "variant_id": card["variant_id"],
                "location_id": card["preselected_location_id"],
                "map_entry_id": card["map_entry_id"],
                "target_ids": selected,
                "topic": f"在{location['name']}核实公开事项和办理进度",
            },
        )
        if started["action"]["location_id"] != location_id:
            raise AssertionError(f"map location lock failed for {location_id}")
        cancelled = _expect(
            client.post(
                f"/api/game/session/{session_id}/governance/actions/"
                f"{started['action']['action_instance_id']}/cancel",
                headers=headers,
                json={"state_version": started["state_version"]},
            )
        )
        if (
            cancelled["visible_state"]["ledger"]["action_points"]["remaining"]
            != before.game_state.action_points
        ):
            raise AssertionError(f"cancelled map action charged AP at {location_id}")
        result = cancelled
        covered.add(location_id)
    return result


def _published_inventory_workflow(runner: RealRouteRunner) -> dict:
    """Exercise every currently compatible published inventory in one legal route."""

    container, client, session_id, headers = runner.build_real_runner(2)
    package = container.packages.get("pkg_gameplay_v3")
    if package is None:
        raise AssertionError("v3 package disappeared during inventory workflow")
    profiles = load_witnesses(PROFILE_PATH)
    profile = _with_recovery_decision_policy(profiles[0])
    contract_terms = load_contract_terms(PROFILE_PATH)
    result, serial = runner.reach_day_three_with_profile(
        container, client, session_id, headers, profile
    )
    all_opportunities = {
        item.opportunity_id for item in package.interaction_opportunities
    }
    all_demands = frozenset(item.demand_id for item in package.npc_demands)
    processed_representatives: set[str] = set()
    map_location_ids: set[str] = set()
    for story_day in range(3, 91):
        if result["visible_state"]["status"] == "ended":
            break
        result = _drain_group_conversations(
            client,
            session_id,
            headers,
            result,
            f"feature-inventory-d{story_day:02d}",
            runner,
        )
        result, serial = runner.drain_profile_decisions(
            container, client, session_id, headers, result, profile, serial
        )
        result, serial = runner.drain_optional_opportunities(
            container,
            client,
            session_id,
            headers,
            result,
            profile,
            serial,
            all_opportunities,
        )
        while True:
            overview = _expect(
                client.get(
                    f"/api/game/session/{session_id}/governance",
                    headers=headers,
                )
            )
            candidates = [
                item for item in overview["npc_demands"]
                if item["demand_id"] in all_demands
                and set(item.get("allowed_transitions", ()))
                & {"acknowledged", "lawfully_refused"}
            ]
            if not candidates:
                break
            demand = candidates[0]
            allowed = set(demand["allowed_transitions"])
            transition = (
                "lawfully_refused"
                if "lawfully_refused" in allowed else "acknowledged"
            )
            disposed = _expect(
                client.post(
                    f"/api/game/session/{session_id}/governance/npc-demands/"
                    f"{demand['demand_id']}/dispose",
                    headers=headers,
                    json={
                        "state_version": result["state_version"],
                        "transition": transition,
                    },
                )
            )
            result = {**result, "state_version": disposed["state_version"]}
        result = runner.inspect_all_available_archives(
            client, session_id, headers, result
        )
        result = runner.sign_contracts_toward_target(
            client,
            session_id,
            headers,
            result,
            target_signed=36,
            contract_terms=contract_terms,
            processed_representatives=processed_representatives,
        )
        if story_day >= 46:
            result = _exercise_all_available_map_locations(
                client,
                container,
                session_id,
                headers,
                result,
                map_location_ids,
            )
        result = runner.end_day(
            client,
            session_id,
            headers,
            result,
            f"feature-inventory-end-d{story_day:02d}",
        )
    stored = container.sessions.get_owned(session_id, headers["X-Account-ID"])
    if stored is None:
        raise AssertionError("inventory workflow session disappeared")
    review = _expect(client.get(f"/api/game/session/{session_id}/review", headers=headers))
    audit = _audit_summary(container, session_id)
    coverage = collect_session_coverage(
        stored, package, map_location_ids=map_location_ids
    )
    payload = {
        "account": "server_default",
        "session_id": session_id,
        "story_day": stored.game_state.story_day,
        "review_available": bool(review),
        "audit": audit,
        "coverage": coverage,
    }
    client.__exit__(None, None, None)
    return payload


def _recovery_opportunity_workflow(runner: RealRouteRunner) -> dict:
    """Reach both mistake-recovery conversations through legal player choices."""

    container, client, session_id, headers = runner.build_real_runner(4)
    package = container.packages.get("pkg_gameplay_v3")
    if package is None:
        raise AssertionError("v3 package disappeared during recovery workflow")
    base_profile = load_witnesses(PROFILE_PATH)[0]
    profile = _with_recovery_decision_policy(replace(
        base_profile,
        route_id="feature-recovery-opportunities",
        decision_policy={
            **base_profile.decision_policy,
            "dp3_06": "c",
            "dp4_06": "d",
            "dp5_04": "d",
        },
    ))
    result, serial = runner.reach_day_three_with_profile(
        container, client, session_id, headers, profile
    )
    recovery_ids = {
        "opp_d53_tan_laoliu_paid_recovery",
        "opp_d69_zhou_mancang_restart",
    }
    recovery_prerequisite_ids = {
        *recovery_ids,
        "opp_03_zhou_mancang_contact",
    }
    for story_day in range(3, 70):
        result = _drain_group_conversations(
            client,
            session_id,
            headers,
            result,
            f"feature-recovery-group-d{story_day:02d}",
            runner,
        )
        result, serial = runner.drain_profile_decisions(
            container, client, session_id, headers, result, profile, serial
        )
        result, serial = runner.drain_optional_opportunities(
            container,
            client,
            session_id,
            headers,
            result,
            profile,
            serial,
            recovery_prerequisite_ids,
        )
        result = runner.inspect_all_available_archives(
            client, session_id, headers, result
        )
        result = runner.end_day(
            client,
            session_id,
            headers,
            result,
            f"feature-recovery-end-d{story_day:02d}",
        )
    stored = container.sessions.get_owned(session_id, headers["X-Account-ID"])
    if stored is None:
        raise AssertionError("recovery workflow session disappeared")
    completed = {
        item.opportunity_id
        for item in stored.completed_conversations
        if item.completion_status == "completed"
    }
    missing = recovery_ids - completed
    if missing:
        raise AssertionError(
            f"legal recovery workflow did not complete: {sorted(missing)}"
        )
    audit = _audit_summary(container, session_id)
    if (
        audit["fake_calls"]
        or any(
            item.get("state_restored") is not True
            for item in runner.operation_retries_by_session.get(session_id, ())
        )
    ):
        raise AssertionError(f"real recovery workflow audit failed: {audit}")
    coverage = collect_session_coverage(stored, package)
    payload = {
        "account": "server_default",
        "session_id": session_id,
        "story_day": stored.game_state.story_day,
        "completed_recovery_opportunity_ids": sorted(recovery_ids),
        "audit": audit,
        "coverage": coverage,
    }
    client.__exit__(None, None, None)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    repository = BACKEND_ROOT.parents[1]
    # Both gates deliberately run before settings validation, output creation, or
    # any TestClient/real-model call.  A failed run therefore cannot be confused
    # with partial feature evidence.
    provenance = capture_run_provenance(repository)
    assert_required_api_evidence_capabilities()
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
        _published_inventory_workflow(runner),
        _recovery_opportunity_workflow(runner),
    ]
    coverage_fields = (
        "archive_ids",
        "fact_acquisition_path_ids",
        "opportunity_ids",
        "npc_ids",
        "map_location_ids",
        "household_ids",
        "governance_action_families",
    )
    report = {
        "provider": "openai_compatible",
        "model": settings.role_llm_model,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "fake_calls": sum(item["audit"]["fake_calls"] for item in workflows),
        "template_fallback_count": sum(
            item["audit"]["template_fallback_count"] for item in workflows
        ),
        "silent_fallback_count": sum(
            item["audit"]["silent_fallback_count"] for item in workflows
        ),
        "partial_commit_count": sum(
            record.get("state_restored") is not True
            for records in runner.operation_retries_by_session.values()
            for record in records
        ),
        "provenance": provenance,
        "server_default_accounts": sum(
            item["account"] == "server_default" for item in workflows
        ),
        "personal_api_accounts": sum(
            item["account"] == "personal" for item in workflows
        ),
        "account_gateway_isolation": (
            len({item["session_id"] for item in workflows}) == len(workflows)
            and {item["account"] for item in workflows}
            == {"server_default", "personal"}
        ),
        **{
            field: sorted({
                value
                for item in workflows
                for value in item["coverage"].get(field, ())
            })
            for field in coverage_fields
        },
        "meeting_completed": any(item.get("meeting_npc_order") for item in workflows),
        "contract_completed": any(item.get("signed_contract_id") for item in workflows),
        "document_completed": any(item.get("document_status") == "issued" for item in workflows),
        "save_load_completed": any(
            item.get("manual_save_load_semantic_equal") is True for item in workflows
        ),
        "review_completed": any(
            item.get("contract_review_status") == "signed"
            or item.get("review_available") is True
            for item in workflows
        ),
        "contract_review_statuses": sorted({
            str(item["contract_review_status"])
            for item in workflows
            if item.get("contract_review_status")
        }),
        "workflows": workflows,
    }
    validate_feature_workflow_report(report)
    (root / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
