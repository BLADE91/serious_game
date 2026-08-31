from __future__ import annotations

import pytest
from pathlib import Path

from serious_game_backend.config import Settings
from serious_game_backend.domain.llm import SelectionOption, SelectionTask
from serious_game_backend.infrastructure.llm.openai_compatible import (
    OpenAICompatibleRoleLLMGateway,
)
from serious_game_backend.infrastructure.repositories.memory import (
    InMemoryLLMCallAuditRepository,
)
from serious_game_backend.domain.llm_runtime import LLMCallAudit
from serious_game_backend.infrastructure.repositories.sqlite import (
    SqliteLLMCallAuditRepository,
    SqliteRuntimeStore,
)
from serious_game_backend.infrastructure.repositories.mysql import (
    MySQLLLMCallAuditRepository,
)
from tools.run_real_feature_workflows import (
    assert_required_api_evidence_capabilities,
    _coverage_operation_records,
    _attach_authority_projections,
    semantic_hash,
    validate_feature_workflow_report,
)


def _built_evidence() -> tuple[dict[str, list[dict]], list[dict]]:
    coverage = {
        "archive_ids": [f"archive-{i:02d}" for i in range(11)],
        "fact_acquisition_path_ids": [
            f"fact-{i:02d}:archive:source-{i:02d}" for i in range(27)
        ],
        "opportunity_ids": [f"opportunity-{i:02d}" for i in range(32)],
        "npc_ids": [f"npc-{i:02d}" for i in range(29)],
        "map_location_ids": [f"location-{i:02d}" for i in range(8)],
        "household_ids": [f"household-{i:02d}" for i in range(36)],
    }
    transitions = []
    specs = (
        ("archive_ids", "archive", "/formal/archive", {"status": "available", "read_at_days": [1]}),
        ("opportunity_ids", "opportunity", "/formal/opportunity", {"completion_status": "completed"}),
        ("map_location_ids", "location", "/formal/finish", {"status": "completed"}),
        ("household_ids", "household", "/formal/review", {"status": "signed"}),
    )
    version = 1
    for field, id_field, path, state in specs:
        for evidence_id in coverage[field]:
            entity = {f"{id_field}_id": evidence_id, **state}
            transitions.append({
                "path": path,
                "status_code": 200,
                "request_hash": semantic_hash({"id": evidence_id}),
                "client_trace_id": None,
                "server_state_version_before": version,
                "server_state_version_after": version + 1,
                "response": entity,
                "readback_effect_hash": semantic_hash({"entity": entity}),
                "readback_state_version": version + 1,
                "readbacks": [{"endpoint": "/formal/get", "payload": entity}],
            })
            version += 1
    workflow = {
        "account": "server_default",
        "story_day": 90,
        "session_id": "session-authority",
        "coverage": coverage,
        "coverage_effect_hash": semantic_hash(coverage),
        "api_traces": transitions,
        "audit": {"records": []},
    }
    workflow["api_traces"].append({
        "path": "/formal/reachability",
        "status_code": 200,
        "request_hash": semantic_hash({"read": "reachability"}),
        "client_trace_id": None,
        "server_state_version_before": version,
        "server_state_version_after": version + 1,
        "response": {
            "npc_ids": coverage["npc_ids"],
            "fact_ids": [f"fact-{i:02d}" for i in range(27)],
            "source_ids": [f"source-{i:02d}" for i in range(27)],
        },
        "readback_effect_hash": semantic_hash(coverage),
        "readback_state_version": version + 1,
        "readbacks": [],
    })
    workflows = [workflow]
    _attach_authority_projections(workflows)
    return _coverage_operation_records(workflows), workflows


def passing_report() -> dict[str, object]:
    operation_records, workflows = _built_evidence()
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
        "fact_acquisition_path_ids": [
            f"fact-{i:02d}:archive:source-{i:02d}" for i in range(27)
        ],
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
            "resolution_hash": semantic_hash({"decision": "adopted"}),
            "document_status": "issued",
            "steps": [
                {"name": name, "evidence_type": "persistent_entity",
                 "persistent_entity_id": f"entity-{name}",
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
                 "model": "model-a", "config_version": "cfg-a",
                 "status": "succeeded", "audit_id": "audit-1"},
                {"account_id": "account-personal", "session_id": "session-b",
                 "mode": "personal", "endpoint_host": "personal.example.test",
                 "model": "model-b", "config_version": "cfg-b",
                 "status": "succeeded", "audit_id": "audit-2"},
            ],
        },
        "save_load_record": {
            "before_semantic_hash": "sha256:" + "e" * 64,
            "after_semantic_hash": "sha256:" + "e" * 64,
            "save_operation_id": "save-1",
            "load_operation_id": "load-1",
        },
        "workflows": workflows,
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


def test_validator_rejects_forged_or_noncommitted_item_record() -> None:
    report = passing_report()
    report["operation_records"]["archives"][0]["evidence_id"] = "forged"
    with pytest.raises(AssertionError, match="archives.*inconsistent"):
        validate_feature_workflow_report(report)
    report = passing_report()
    report["operation_records"]["archives"][0]["status"] = "failed"
    with pytest.raises(AssertionError, match="archives.*succeed"):
        validate_feature_workflow_report(report)


def test_validator_rejects_cross_account_gateway_mixup() -> None:
    report = passing_report()
    records = report["gateway_audit"]["records"]
    records[1]["account_id"] = records[0]["account_id"]
    with pytest.raises(AssertionError, match="account gateway"):
        validate_feature_workflow_report(report)


def test_validator_rejects_meeting_audit_not_in_gateway_evidence() -> None:
    report = passing_report()
    report["meeting_document_record"]["llm_audit_ids"] = ["forged-audit"]
    with pytest.raises(AssertionError, match="meeting.*gateway"):
        validate_feature_workflow_report(report)


def test_validator_rejects_tampered_entity_projection_and_illegal_downgrade() -> None:
    report = passing_report()
    archive = report["operation_records"]["archives"][0]
    archive["entity_projection"].pop("archive_id")
    with pytest.raises(AssertionError, match="archives.*ID"):
        validate_feature_workflow_report(report)


def test_reachability_is_bound_to_workflow_authority_projection() -> None:
    report = passing_report()
    report["npc_ids"][0] = "npc-forged"
    report["operation_records"]["npcs"][0]["evidence_id"] = "npc-forged"
    with pytest.raises(AssertionError, match="selector|workflow projection"):
        validate_feature_workflow_report(report)

    report = passing_report()
    projection = report["workflows"][0]["authority_projection"]
    projection["items"]["npcs"][0]["evidence_id"] = "npc-tampered"
    with pytest.raises(AssertionError, match="provenance"):
        validate_feature_workflow_report(report)

    report = passing_report()
    record = report["operation_records"]["npcs"][0]
    record["source_item_selector"] = "unknown-selector"
    with pytest.raises(AssertionError, match="projection"):
        validate_feature_workflow_report(report)

    report = passing_report()
    archive = report["operation_records"]["archives"][0]
    archive["entity_projection"]["read_at_days"] = []
    archive["entity_projection_hash"] = semantic_hash(archive["entity_projection"])
    with pytest.raises(AssertionError, match="not read"):
        validate_feature_workflow_report(report)

    report = passing_report()
    report["operation_records"]["archives"][0]["entity_projection_hash"] = (
        "sha256:" + "f" * 64
    )
    with pytest.raises(AssertionError, match="projection hash"):
        validate_feature_workflow_report(report)

    report = passing_report()
    report["operation_records"]["archives"][0]["evidence_kind"] = (
        "authoritative_reachability"
    )
    with pytest.raises(AssertionError, match="kind is not allowed"):
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


def _cache_task(*, account: str, session: str) -> SelectionTask:
    return SelectionTask(
        task_id="cache", role_id="npc", role_name="NPC", instruction="select",
        options=(SelectionOption("a", "A"),), selection_mode="single",
        minimum_choices=1, maximum_choices=1, session_id=session,
        account_id=account, operation_id="same-operation", story_day=1,
    )


def test_success_cache_never_crosses_account_or_session_boundary() -> None:
    audits = InMemoryLLMCallAuditRepository()
    settings = Settings(environment="test", role_llm_provider="openai_compatible")
    calls = []
    transport = lambda *_args: (
        calls.append(1) or {
            "choices": [{"message": {"content": '{"choice_id":"a"}'}}],
            "usage": {},
        }
    )
    gateway = OpenAICompatibleRoleLLMGateway(
        settings, "secret", audits, config_version="cfg-a", transport=transport,
    )
    gateway.select(_cache_task(account="account-a", session="session-a"))
    gateway.select(_cache_task(account="account-b", session="session-b"))

    assert len(calls) == 2
    assert {item.account_id for item in audits.list_for_session("session-a")} == {"account-a"}
    assert {item.account_id for item in audits.list_for_session("session-b")} == {"account-b"}


def test_success_cache_never_crosses_config_version_after_reconfiguration() -> None:
    audits = InMemoryLLMCallAuditRepository()
    settings = Settings(environment="test", role_llm_provider="openai_compatible")
    calls = []
    transport = lambda *_args: (
        calls.append(1) or {
            "choices": [{"message": {"content": '{"choice_id":"a"}'}}],
            "usage": {},
        }
    )
    old = OpenAICompatibleRoleLLMGateway(
        settings, "old-secret", audits, config_version="cfg-old", transport=transport,
    )
    new = OpenAICompatibleRoleLLMGateway(
        settings, "new-secret", audits, config_version="cfg-new", transport=transport,
    )
    task = _cache_task(account="account-a", session="session-a")
    old.select(task)
    new.select(task)
    old.select(task)

    assert len(calls) == 2
    saved = audits.list_for_session("session-a")
    assert [item.config_version for item in saved] == [
        "cfg-old", "cfg-new", "cfg-old",
    ]
    assert saved[-1].status == "cached"
    assert saved[-1].source_audit_id == saved[0].audit_id


def test_transport_failure_writes_secret_free_frozen_audit() -> None:
    from serious_game_backend.domain.errors import RoleLLMUnavailableError

    audits = InMemoryLLMCallAuditRepository()
    settings = Settings(
        environment="test", role_llm_provider="openai_compatible",
        role_llm_max_retries=0,
    )
    gateway = OpenAICompatibleRoleLLMGateway(
        settings, "sentinel-secret", audits,
        audit_endpoint_host="api.example.test", config_version="cfg-failure",
        transport=lambda *_args: (_ for _ in ()).throw(
            RoleLLMUnavailableError("sentinel-secret full-url https://bad/path?key=x")
        ),
    )
    with pytest.raises(RoleLLMUnavailableError):
        gateway.select(_cache_task(account="account-a", session="session-a"))

    audit = audits.list_for_session("session-a")[0]
    assert audit.status == "failed"
    assert audit.error_code == "ROLE_LLM_UNAVAILABLE"
    assert audit.endpoint_host == "api.example.test"
    assert audit.config_version == "cfg-failure"
    assert "sentinel-secret" not in repr(audit)
    assert "https://" not in repr(audit)


@pytest.mark.parametrize("repository_kind", ["memory", "sqlite"])
def test_owned_audit_pagination_handles_50_plus_same_timestamp(
    repository_kind: str, tmp_path: Path,
) -> None:
    repository = (
        InMemoryLLMCallAuditRepository()
        if repository_kind == "memory"
        else SqliteLLMCallAuditRepository(SqliteRuntimeStore(tmp_path / "audit.db"))
    )
    for index in range(55):
        repository.save(LLMCallAudit(
            audit_id=f"audit-{index:03d}", session_id="session-a",
            account_id="account-a", operation_id=f"operation-{index:03d}",
            story_day=1, npc_id="npc", provider="openai_compatible",
            model_id="model", prompt_version="v1", request_hash=f"hash-{index}",
            status="succeeded", endpoint_host="api.example.test",
            config_version="cfg-a", created_at="2026-08-31T00:00:00+00:00",
        ))
    repository.save(LLMCallAudit(
        audit_id="audit-intruder", session_id="session-a", account_id="account-b",
        operation_id="operation-intruder", story_day=1, npc_id="npc",
        provider="openai_compatible", model_id="model", prompt_version="v1",
        request_hash="intruder", status="succeeded",
    ))

    first = repository.list_for_owned_session(
        "account-a", "session-a", after="", limit=50
    )
    cursor = f"{first[-1].created_at}|{first[-1].audit_id}"
    second = repository.list_for_owned_session(
        "account-a", "session-a", after=cursor, limit=50
    )

    assert len(first) == 50
    assert len(second) == 5
    assert len({item.audit_id for item in (*first, *second)}) == 55
    assert all(item.account_id == "account-a" for item in (*first, *second))


def test_mysql_owned_pagination_sql_binds_owner_cursor_and_limit() -> None:
    calls = []

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def execute(self, sql, params): calls.append((sql, params))
        def fetchall(self): return []

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def cursor(self): return Cursor()

    class Store:
        def connect(self): return Connection()

    result = MySQLLLMCallAuditRepository(Store()).list_for_owned_session(
        "account-a", "session-a", after="cursor-a", limit=50
    )

    assert result == ()
    sql, params = calls[0]
    assert "account_id=%s and session_id=%s" in " ".join(sql.split())
    assert "limit %s" in " ".join(sql.split()).casefold()
    assert params == ("account-a", "session-a", "cursor-a", 50)
