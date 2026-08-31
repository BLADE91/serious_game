from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from serious_game_backend.infrastructure.script_packages.file_loader import (
    FileScriptPackageLoader,
)
from tools.run_story_routes_v3_isolated import (
    build_run_metadata,
    ensure_backend_import_path,
    finalize_summary,
    repository_root_for_backend,
    static_route_shards,
)


def _route_ids(count: int = 95) -> list[str]:
    return [f"route-ending-{index:03d}" for index in range(count)]


def _passing_route(route_id: str, ending_index: int) -> dict[str, object]:
    return {
        "route_id": route_id,
        "expected_main": f"main-{ending_index % 24:02d}",
        "expected_sub": f"sub-{ending_index:03d}",
        "story_day": 90,
        "main_ending_id": f"main-{ending_index % 24:02d}",
        "sub_ending_id": f"sub-{ending_index:03d}",
        "duration_seconds": 1.0,
        "status": "passed",
    }


def test_static_route_shards_assign_every_route_once_and_balance_workers() -> None:
    route_ids = _route_ids()

    shards = static_route_shards(route_ids, worker_count=8)

    assert [len(shard) for shard in shards] == [12, 12, 12, 12, 12, 12, 12, 11]
    flattened = [route_id for shard in shards for route_id in shard]
    assert len(flattened) == 95
    assert set(flattened) == set(route_ids)


def test_file_entrypoint_adds_backend_root_to_python_import_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend_root = tmp_path / "code" / "backend"
    backend_root.mkdir(parents=True)
    monkeypatch.setattr("sys.path", [str(backend_root / "tools")])

    ensure_backend_import_path(backend_root)

    assert str(backend_root) == __import__("sys").path[0]


def test_repository_root_is_two_directories_above_backend() -> None:
    backend_root = Path("E:/workspace/code/backend")

    assert repository_root_for_backend(backend_root) == Path("E:/workspace")


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda rows: rows[:-1], "missing route"),
        (lambda rows: rows + [dict(rows[0])], "duplicate route"),
    ],
)
def test_finalize_summary_rejects_missing_or_duplicate_routes(mutate, message: str) -> None:
    route_ids = _route_ids()
    rows = [_passing_route(route_id, index) for index, route_id in enumerate(route_ids)]

    with pytest.raises(ValueError, match=message):
        finalize_summary(
            expected_route_ids=route_ids,
            worker_results=[{"worker_id": 0, "exit_code": 0, "routes": mutate(rows)}],
            metadata={"git_sha": "abc"},
            worker_count=8,
            duration_seconds=2.0,
        )


def test_finalize_summary_rejects_worker_failure_even_with_complete_routes() -> None:
    route_ids = _route_ids()
    rows = [_passing_route(route_id, index) for index, route_id in enumerate(route_ids)]

    with pytest.raises(RuntimeError, match="worker 3 failed"):
        finalize_summary(
            expected_route_ids=route_ids,
            worker_results=[
                {"worker_id": 0, "exit_code": 0, "routes": rows},
                {"worker_id": 3, "exit_code": 7, "routes": [], "error": "boom"},
            ],
            metadata={"git_sha": "abc"},
            worker_count=8,
            duration_seconds=2.0,
        )


def test_build_run_metadata_binds_git_workspace_v3_and_test_collection(tmp_path: Path) -> None:
    backend_root = tmp_path / "code" / "backend"
    v3_root = backend_root / "content" / "packages" / "pkg_gameplay_v3"
    source_v3_root = (
        Path(__file__).resolve().parents[1]
        / "content"
        / "packages"
        / "pkg_gameplay_v3"
    )
    test_file = backend_root / "tests" / "test_story_routes_v3.py"
    shutil.copytree(source_v3_root, v3_root)
    test_file.parent.mkdir(parents=True)
    test_file.write_bytes(b"# formal replay\n")
    route_ids = ["route-b", "route-a"]

    metadata = build_run_metadata(
        repo_root=tmp_path,
        backend_root=backend_root,
        route_ids=route_ids,
        git_sha="deadbeef",
        workspace_fingerprint="workspace123",
    )

    expected_v3_hash = json.loads(
        (v3_root / "package_manifest.json").read_text(encoding="utf-8")
    )["content_hash"]
    expected_collection_hash = hashlib.sha256(
        b"route-b\nroute-a\n\0# formal replay\n"
    ).hexdigest()
    assert metadata == {
        "git_sha": "deadbeef",
        "workspace_fingerprint": "workspace123",
        "v3_hash": expected_v3_hash,
        "v3_raw_hash": FileScriptPackageLoader.compute_content_hash(v3_root),
        "v3_portable_hash": FileScriptPackageLoader.compute_portable_content_hash(
            v3_root
        ),
        "v3_package_identity_verified": True,
        "test_collection_hash": expected_collection_hash,
        "route_count": 2,
    }


def test_finalize_summary_emits_sorted_machine_readable_95_route_manifest() -> None:
    route_ids = _route_ids()
    rows = [_passing_route(route_id, index) for index, route_id in enumerate(route_ids)]
    summary = finalize_summary(
        expected_route_ids=route_ids,
        worker_results=[{"worker_id": 0, "exit_code": 0, "routes": list(reversed(rows))}],
        metadata={"git_sha": "abc"},
        worker_count=8,
        duration_seconds=12.5,
    )

    assert summary["status"] == "passed"
    assert summary["worker_count"] == 8
    assert summary["route_count"] == 95
    assert summary["main_ending_count"] == 24
    assert summary["sub_ending_count"] == 95
    assert [row["route_id"] for row in summary["routes"]] == sorted(route_ids)
    json.dumps(summary, ensure_ascii=False)
