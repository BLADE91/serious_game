from __future__ import annotations

from pathlib import Path

import pytest

from serious_game_backend.config import Settings
from serious_game_backend.infrastructure.script_packages.file_loader import (
    FileScriptPackageLoader,
)
from tools.full_acceptance.ending_witnesses import load_witnesses
from tools.run_full_acceptance import run_stages
from tools.run_real_feature_workflows import validate_feature_workflow_report
from tools.run_real_v3_routes import (
    prepare_output_run,
    validate_profile_catalog,
    validate_real_runner_settings,
    validate_route_result,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = BACKEND_ROOT / "content" / "packages" / "pkg_gameplay_v3"
PROFILE_PATH = PACKAGE_ROOT / "acceptance_route_profiles.json"


def test_real_runner_refuses_fake_provider_fallback_and_missing_key() -> None:
    with pytest.raises(SystemExit, match="requires openai_compatible"):
        validate_real_runner_settings(
            Settings(role_llm_provider="fake"), api_key="real-key-present"
        )
    with pytest.raises(SystemExit, match="refuses Fake fallback"):
        validate_real_runner_settings(
            Settings(
                role_llm_provider="openai_compatible",
                role_llm_fallback_to_fake=True,
            ),
            api_key="real-key-present",
        )
    with pytest.raises(SystemExit, match="API key is missing"):
        validate_real_runner_settings(
            Settings(role_llm_provider="openai_compatible"), api_key=""
        )


def test_real_runner_requires_a_complete_profile_catalog() -> None:
    package = FileScriptPackageLoader().load(PACKAGE_ROOT)
    profiles = load_witnesses(PROFILE_PATH)

    validate_profile_catalog(profiles, package)

    with pytest.raises(ValueError, match="95"):
        validate_profile_catalog(profiles[:-1], package)


def test_real_runner_refuses_reused_evidence_directories(tmp_path: Path) -> None:
    run_root = prepare_output_run(tmp_path, run_id="fixed-run")

    assert run_root == tmp_path / "fixed-run"
    with pytest.raises(FileExistsError):
        prepare_output_run(tmp_path, run_id="fixed-run")


def _passing_route_result(profile) -> dict[str, object]:
    return {
        "route_id": profile.route_id,
        "story_day": 90,
        "status": "ended",
        "main_ending_id": profile.target_main_ending_ids[0],
        "sub_ending_id": profile.target_sub_ending_ids[0],
        "visited_days": list(range(1, 91)),
        "llm_audits": 12,
        "providers": {"openai_compatible": 12},
        "fake_calls": 0,
        "template_fallback_count": 0,
        "silent_fallback_count": 0,
        "partial_commit_count": 0,
        "direct_state_writes": 0,
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("main_ending_id", "ending_99", "main ending"),
        ("sub_ending_id", "ending_99z", "sub ending"),
        ("visited_days", [1, 2, 4, 90], "D1-D90"),
        ("llm_audits", 0, "model audit"),
        ("fake_calls", 1, "Fake"),
        ("template_fallback_count", 1, "template fallback"),
        ("silent_fallback_count", 1, "silent fallback"),
        ("partial_commit_count", 1, "partial commit"),
        ("direct_state_writes", 1, "direct state"),
    ),
)
def test_real_runner_rejects_invalid_route_evidence(
    field: str,
    value: object,
    message: str,
) -> None:
    profile = load_witnesses(PROFILE_PATH)[0]
    result = _passing_route_result(profile)
    result[field] = value

    with pytest.raises(AssertionError, match=message):
        validate_route_result(profile, result)


def test_feature_workflow_report_requires_every_published_system() -> None:
    complete = {
        "provider": "openai_compatible",
        "fake_calls": 0,
        "server_default_accounts": 1,
        "personal_api_accounts": 1,
        "account_gateway_isolation": True,
        "archive_ids": [f"archive-{index}" for index in range(11)],
        "fact_acquisition_path_ids": [f"fact-path-{index}" for index in range(27)],
        "opportunity_ids": [f"opportunity-{index}" for index in range(32)],
        "npc_ids": [f"npc-{index}" for index in range(29)],
        "map_location_ids": [f"map-{index}" for index in range(8)],
        "household_ids": [f"household-{index}" for index in range(36)],
        "governance_action_families": [
            "inspect_archives",
            "household_visit",
            "cadre_interview",
            "leadership_meeting",
        ],
        "meeting_completed": True,
        "contract_completed": True,
        "document_completed": True,
        "save_load_completed": True,
        "review_completed": True,
    }

    validate_feature_workflow_report(complete)

    incomplete = {**complete, "household_ids": complete["household_ids"][:-1]}
    with pytest.raises(AssertionError, match="36 households"):
        validate_feature_workflow_report(incomplete)


def test_full_acceptance_stops_after_the_first_failed_stage() -> None:
    calls: list[str] = []

    def executor(name: str, command: tuple[str, ...]) -> int:
        calls.append(name)
        return 3 if name == "features" else 0

    with pytest.raises(SystemExit, match="features"):
        run_stages(
            (
                ("capabilities", ("python", "capabilities.py")),
                ("features", ("python", "features.py")),
                ("routes", ("python", "routes.py")),
            ),
            executor=executor,
        )

    assert calls == ["capabilities", "features"]
