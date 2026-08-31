from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import gc
import hashlib
import json
import multiprocessing
from pathlib import Path
import queue
import runpy
import subprocess
import sys
import time
import traceback
from typing import Any, Sequence


DEFAULT_WORKER_COUNT = 8
EXPECTED_ROUTE_COUNT = 95
EXPECTED_MAIN_ENDING_COUNT = 24
EXPECTED_SUB_ENDING_COUNT = 95


def ensure_backend_import_path(backend_root: Path) -> None:
    backend_path = str(backend_root)
    if backend_path in sys.path:
        sys.path.remove(backend_path)
    sys.path.insert(0, backend_path)


def repository_root_for_backend(backend_root: Path) -> Path:
    return backend_root.parents[1]


def static_route_shards(route_ids: Sequence[str], worker_count: int) -> list[list[str]]:
    if worker_count < 1:
        raise ValueError("worker_count must be positive")
    shards: list[list[str]] = [[] for _ in range(worker_count)]
    for index, route_id in enumerate(route_ids):
        shards[index % worker_count].append(route_id)
    return shards


def _hash_files(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _git_output(repo_root: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout


def current_git_sha(repo_root: Path) -> str:
    return _git_output(repo_root, "rev-parse", "HEAD").decode("ascii").strip()


def current_workspace_fingerprint(repo_root: Path, backend_root: Path) -> str:
    """Bind the run to every tracked or untracked Backend file byte-for-byte."""
    relative_backend = backend_root.relative_to(repo_root).as_posix()
    listed = _git_output(
        repo_root,
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "--",
        relative_backend,
    )
    paths = sorted(line for line in listed.decode("utf-8").splitlines() if line)
    digest = hashlib.sha256()
    digest.update(current_git_sha(repo_root).encode("ascii"))
    digest.update(b"\0")
    for relative in paths:
        path = repo_root / relative
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def build_run_metadata(
    *,
    repo_root: Path,
    backend_root: Path,
    route_ids: Sequence[str],
    git_sha: str | None = None,
    workspace_fingerprint: str | None = None,
) -> dict[str, object]:
    test_file = backend_root / "tests" / "test_story_routes_v3.py"
    collection_digest = hashlib.sha256()
    collection_digest.update(("\n".join(route_ids) + "\n").encode("utf-8"))
    collection_digest.update(b"\0")
    collection_digest.update(test_file.read_bytes())
    return {
        "git_sha": git_sha or current_git_sha(repo_root),
        "workspace_fingerprint": workspace_fingerprint
        or current_workspace_fingerprint(repo_root, backend_root),
        "v3_hash": _hash_files(
            backend_root / "content" / "packages" / "pkg_gameplay_v3"
        ),
        "test_collection_hash": collection_digest.hexdigest(),
        "route_count": len(route_ids),
    }


def _hash_json(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _minimal_route_result(witness: dict[str, Any], duration_seconds: float) -> dict[str, Any]:
    keys = (
        "route_id",
        "expected_main",
        "expected_sub",
        "story_day",
        "main_ending_id",
        "sub_ending_id",
        "ledger",
        "axes",
    )
    result = {key: witness[key] for key in keys}
    result["duration_seconds"] = round(duration_seconds, 6)
    result["status"] = "passed"
    return result


def _worker_main(
    worker_id: int,
    route_ids: list[str],
    test_file: str,
    result_queue: multiprocessing.Queue,
) -> None:
    started = time.perf_counter()
    routes: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    try:
        ensure_backend_import_path(Path(test_file).resolve().parents[1])
        namespace = runpy.run_path(test_file)
        test_case_class = namespace["StoryRoutesV3Tests"]
        witness_path = namespace["WITNESS_PROFILE_PATH"]
        profiles = namespace["load_witnesses"](witness_path)
        contract_terms = namespace["load_contract_terms"](witness_path)
        profiles_by_id = {profile.route_id: profile for profile in profiles}
        route_indexes = {
            profile.route_id: index for index, profile in enumerate(profiles, start=100)
        }
        for route_id in route_ids:
            route_started = time.perf_counter()
            try:
                case = test_case_class(methodName="runTest")
                with redirect_stdout(sys.stderr):
                    witness = case.replay_published_witness(
                        route_indexes[route_id], profiles_by_id[route_id], contract_terms
                    )
                routes.append(
                    _minimal_route_result(witness, time.perf_counter() - route_started)
                )
            except BaseException as exc:  # noqa: BLE001 - preserve every route failure
                errors.append(
                    {
                        "route_id": route_id,
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "traceback": traceback.format_exc(),
                    }
                )
            finally:
                gc.collect()
    except BaseException as exc:  # noqa: BLE001 - return startup/import failures to parent
        errors.append(
            {
                "route_id": "<worker-startup>",
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            }
        )
    envelope = {
        "worker_id": worker_id,
        "exit_code": 1 if errors else 0,
        "input_hash": _hash_json(route_ids),
        "output_hash": _hash_json(routes),
        "duration_seconds": round(time.perf_counter() - started, 6),
        "routes": routes,
        "errors": errors,
    }
    result_queue.put(envelope)


def finalize_summary(
    *,
    expected_route_ids: Sequence[str],
    worker_results: Sequence[dict[str, Any]],
    metadata: dict[str, object],
    worker_count: int,
    duration_seconds: float,
) -> dict[str, Any]:
    for worker in worker_results:
        if worker.get("exit_code") != 0:
            detail = worker.get("error") or worker.get("errors") or "unknown error"
            raise RuntimeError(f"worker {worker.get('worker_id')} failed: {detail}")

    routes = [route for worker in worker_results for route in worker.get("routes", [])]
    actual_ids = [str(route.get("route_id")) for route in routes]
    duplicates = sorted({route_id for route_id in actual_ids if actual_ids.count(route_id) > 1})
    if duplicates:
        raise ValueError(f"duplicate route ids: {duplicates}")
    expected = set(expected_route_ids)
    actual = set(actual_ids)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing:
        raise ValueError(f"missing route ids: {missing}")
    if unexpected:
        raise ValueError(f"unexpected route ids: {unexpected}")
    if len(expected_route_ids) != EXPECTED_ROUTE_COUNT or len(expected) != EXPECTED_ROUTE_COUNT:
        raise ValueError(
            f"expected route collection must contain exactly {EXPECTED_ROUTE_COUNT} unique ids"
        )

    route_failures: list[str] = []
    for route in routes:
        route_id = route["route_id"]
        if route.get("status") != "passed":
            route_failures.append(f"{route_id}: status={route.get('status')}")
        if route.get("story_day") != 90:
            route_failures.append(f"{route_id}: ended on D{route.get('story_day')}")
        if route.get("main_ending_id") != route.get("expected_main"):
            route_failures.append(f"{route_id}: main ending mismatch")
        if route.get("sub_ending_id") != route.get("expected_sub"):
            route_failures.append(f"{route_id}: sub ending mismatch")
    if route_failures:
        raise ValueError("route validation failed: " + "; ".join(route_failures))

    main_count = len({route["main_ending_id"] for route in routes})
    sub_count = len({route["sub_ending_id"] for route in routes})
    if main_count != EXPECTED_MAIN_ENDING_COUNT:
        raise ValueError(f"main ending coverage {main_count}/{EXPECTED_MAIN_ENDING_COUNT}")
    if sub_count != EXPECTED_SUB_ENDING_COUNT:
        raise ValueError(f"sub ending coverage {sub_count}/{EXPECTED_SUB_ENDING_COUNT}")

    return {
        "status": "passed",
        **metadata,
        "worker_count": worker_count,
        "duration_seconds": round(duration_seconds, 6),
        "route_count": len(routes),
        "main_ending_count": main_count,
        "sub_ending_count": sub_count,
        "workers": [
            {key: value for key, value in worker.items() if key != "routes"}
            for worker in sorted(worker_results, key=lambda item: item["worker_id"])
        ],
        "routes": sorted(routes, key=lambda item: item["route_id"]),
    }


def run_isolated(
    worker_count: int = DEFAULT_WORKER_COUNT, output_path: Path | None = None
) -> dict[str, Any]:
    backend_root = Path(__file__).resolve().parents[1]
    repo_root = repository_root_for_backend(backend_root)
    test_file = backend_root / "tests" / "test_story_routes_v3.py"
    ensure_backend_import_path(backend_root)
    namespace = runpy.run_path(str(test_file))
    witness_path = namespace["WITNESS_PROFILE_PATH"]
    profiles = namespace["load_witnesses"](witness_path)
    route_ids = [profile.route_id for profile in profiles]
    if len(route_ids) != EXPECTED_ROUTE_COUNT or len(set(route_ids)) != EXPECTED_ROUTE_COUNT:
        raise ValueError("witness profile must contain exactly 95 unique route ids")

    metadata = build_run_metadata(
        repo_root=repo_root,
        backend_root=backend_root,
        route_ids=route_ids,
    )
    shards = static_route_shards(route_ids, worker_count)
    context = multiprocessing.get_context("spawn")
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_worker_main,
            args=(worker_id, shard, str(test_file), result_queue),
            name=f"story-route-worker-{worker_id}",
        )
        for worker_id, shard in enumerate(shards)
    ]
    started = time.perf_counter()
    for process in processes:
        process.start()
    worker_results: list[dict[str, Any]] = []
    remaining = len(processes)
    while remaining:
        try:
            worker_results.append(result_queue.get(timeout=1.0))
            remaining -= 1
        except queue.Empty:
            if not any(process.is_alive() for process in processes):
                break
    for process in processes:
        process.join()
    reported_workers = {result["worker_id"] for result in worker_results}
    for worker_id, process in enumerate(processes):
        if worker_id not in reported_workers or process.exitcode != 0:
            worker_results.append(
                {
                    "worker_id": worker_id,
                    "exit_code": process.exitcode if process.exitcode != 0 else 1,
                    "routes": [],
                    "error": "worker exited without a valid result envelope",
                }
            )
    summary = finalize_summary(
        expected_route_ids=route_ids,
        worker_results=worker_results,
        metadata=metadata,
        worker_count=worker_count,
        duration_seconds=time.perf_counter() - started,
    )
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Replay all 95 V3 ending witnesses in persistent isolated processes."
    )
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKER_COUNT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        summary = run_isolated(args.workers, args.output)
    except BaseException as exc:  # noqa: BLE001 - CLI must fail closed
        summary = {
            "status": "failed",
            "worker_count": args.workers,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }
        exit_code = 1
    else:
        exit_code = 0
    rendered = json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2)
    print(rendered)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
