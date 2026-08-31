from __future__ import annotations

import pytest

from serious_game_backend.config import Settings
from serious_game_backend.domain.llm import SelectionOption, SelectionTask
from serious_game_backend.infrastructure.llm.openai_compatible import (
    OpenAICompatibleRoleLLMGateway,
)
from serious_game_backend.infrastructure.repositories.memory import (
    InMemoryLLMCallAuditRepository,
)
from tools.run_real_feature_workflows import (
    assert_required_api_evidence_capabilities,
    validate_feature_workflow_report,
)


def _records(prefix: str, count: int) -> list[dict[str, object]]:
    return [
        {
            "evidence_id": f"{prefix}-{index:02d}",
            "operation_id": f"op-{prefix}-{index:02d}",
            "before_version": index,
            "after_version": index + 1,
            "status": "succeeded",
        }
        for index in range(count)
    ]


def passing_report() -> dict[str, object]:
    operation_records = {
        "households": _records("household", 36),
        "archives": _records("archive", 11),
        "fact_acquisition_paths": _records("path", 27),
        "opportunities": _records("opportunity", 32),
        "npcs": _records("npc", 29),
        "map_locations": _records("location", 8),
    }
    return {
        "provider": "openai_compatible",
        "fake_calls": 0,
        "template_fallback_count": 0,
        "silent_fallback_count": 0,
        "partial_commit_count": 0,
        "server_default_accounts": 1,
        "personal_api_accounts": 1,
        "account_gateway_isolation": True,
        "archive_ids": [f"archive-{i:02d}" for i in range(11)],
        "fact_acquisition_path_ids": [f"path-{i:02d}" for i in range(27)],
        "opportunity_ids": [f"opportunity-{i:02d}" for i in range(32)],
        "npc_ids": [f"npc-{i:02d}" for i in range(29)],
        "map_location_ids": [f"location-{i:02d}" for i in range(8)],
        "household_ids": [f"household-{i:02d}" for i in range(36)],
        "governance_action_families": [
            "inspect_archives", "household_visit", "cadre_interview",
            "leadership_meeting",
        ],
        "meeting_completed": True,
        "contract_completed": True,
        "document_completed": True,
        "save_load_completed": True,
        "review_completed": True,
        "contract_review_statuses": ["signed"],
        "provenance": {
            "git_commit": "a" * 40,
            "tracked_workspace_clean": True,
            "workspace_fingerprint": "b" * 64,
            "v3_manifest_hash": "sha256:" + "c" * 64,
            "v3_computed_hash": "sha256:" + "c" * 64,
        },
        "meeting_document_record": {
            "source_meeting_id": "meeting-1",
            "meeting_id": "meeting-1",
            "resolution_snapshot": {"decision": "adopted"},
            "resolution_hash": "sha256:" + "d" * 64,
            "document_status": "issued",
            "steps": [
                {"name": name, "operation_id": f"op-{name}",
                 "before_version": i, "after_version": i + 1}
                for i, name in enumerate(
                    ("meeting", "turn", "resolve", "countersign", "issue")
                )
            ],
            "llm_audit_ids": ["audit-1"],
        },
        "operation_records": operation_records,
        "gateway_audit": {
            "interleaved": True,
            "records": [
                {"account_id": "account-server", "session_id": "session-a",
                 "mode": "server_default", "endpoint_host": "api.example.test",
                 "model": "model-a", "config_version": "cfg-a"},
                {"account_id": "account-personal", "session_id": "session-b",
                 "mode": "personal", "endpoint_host": "personal.example.test",
                 "model": "model-b", "config_version": "cfg-b"},
            ],
        },
        "save_load_record": {
            "before_semantic_hash": "sha256:" + "e" * 64,
            "after_semantic_hash": "sha256:" + "e" * 64,
            "save_operation_id": "save-1",
            "load_operation_id": "load-1",
        },
    }


@pytest.mark.parametrize("field", [
    "fake_calls", "template_fallback_count", "silent_fallback_count",
    "partial_commit_count",
])
def test_validator_rejects_nonzero_or_missing_source_counts(field: str) -> None:
    report = passing_report()
    report.pop(field)
    with pytest.raises(AssertionError, match=field):
        validate_feature_workflow_report(report)
    report = passing_report()
    report[field] = 1
    with pytest.raises(AssertionError, match=field):
        validate_feature_workflow_report(report)


def test_validator_rejects_tampered_sha_or_package_hash() -> None:
    report = passing_report()
    report["provenance"]["git_commit"] = "not-a-sha"
    with pytest.raises(AssertionError, match="git_commit"):
        validate_feature_workflow_report(report)
    report = passing_report()
    report["provenance"]["v3_computed_hash"] = "sha256:" + "f" * 64
    with pytest.raises(AssertionError, match="v3 content hash"):
        validate_feature_workflow_report(report)


def test_validator_rejects_missing_item_operation_record() -> None:
    report = passing_report()
    report["operation_records"]["households"].pop()
    with pytest.raises(AssertionError, match="households operation records"):
        validate_feature_workflow_report(report)


def test_validator_rejects_cross_account_gateway_mixup() -> None:
    report = passing_report()
    records = report["gateway_audit"]["records"]
    records[1]["account_id"] = records[0]["account_id"]
    with pytest.raises(AssertionError, match="account gateway"):
        validate_feature_workflow_report(report)


def test_validator_accepts_complete_real_feature_evidence_contract() -> None:
    validate_feature_workflow_report(passing_report())


def test_preflight_stops_when_per_call_gateway_provenance_is_unavailable() -> None:
    assert_required_api_evidence_capabilities()


def test_gateway_audit_freezes_redacted_host_and_opaque_config_version() -> None:
    sentinel = "sentinel-secret-key-never-serialize"
    audits = InMemoryLLMCallAuditRepository()
    settings = Settings(
        environment="test",
        role_llm_provider="openai_compatible",
        role_llm_base_url="https://user:password@api.example.test/v1?token=forbidden",
        role_llm_model="model-a",
        role_llm_max_retries=0,
    )
    gateway = OpenAICompatibleRoleLLMGateway(
        settings, sentinel, audits,
        audit_endpoint_host="api.example.test",
        config_version="cfg_test_version",
        transport=lambda *_args: {
            "choices": [{"message": {"content": '{"choice_id":"a"}'}}],
            "usage": {},
        },
    )
    gateway.select(SelectionTask(
        task_id="task", role_id="npc", role_name="NPC", instruction="select",
        options=(SelectionOption("a", "A"),), selection_mode="single",
        minimum_choices=1, maximum_choices=1, session_id="session-a",
        account_id="account-a", operation_id="operation-a", story_day=1,
    ))

    audit = audits.list_for_session("session-a")[0]
    assert audit.endpoint_host == "api.example.test"
    assert audit.config_version == "cfg_test_version"
    serialized = repr(audit)
    assert sentinel not in serialized
    assert "password" not in serialized
    assert "token=forbidden" not in serialized
