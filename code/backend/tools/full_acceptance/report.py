from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable

from tools.full_acceptance.coverage_contract import CoverageContract
from tools.full_acceptance.evidence_store import (
    EvidenceManifestError,
    EvidenceRecord,
    EvidenceStore,
)


@dataclass(frozen=True, slots=True)
class ReleaseReport:
    publishable: bool
    blockers: tuple[str, ...]
    required_evidence: int
    covered_evidence: int
    missing_evidence_ids: tuple[str, ...]
    invalid_evidence_ids: tuple[str, ...]
    provenance: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_count(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    return 0


def _failed_calls_are_recovered(value: Any, metadata: dict[str, Any]) -> bool:
    if _as_count(value) == 0:
        return True
    if metadata.get("state_restored") is True:
        return True
    if isinstance(value, list) and value:
        return all(
            isinstance(item, dict) and item.get("state_restored") is True
            for item in value
        )
    return False


def _record_key(record: EvidenceRecord) -> str:
    return f"{record.coverage_id}:{record.evidence_type}"


def _artifact_is_valid(store: EvidenceStore, record: EvidenceRecord) -> bool:
    relative = Path(record.artifact_path)
    if relative.is_absolute():
        return False
    artifact = (store.root / relative).resolve()
    try:
        artifact.relative_to(store.root)
    except ValueError:
        return False
    return artifact.is_file() and sha256(artifact.read_bytes()).hexdigest() == record.sha256


def _provenance_values(
    records: Iterable[EvidenceRecord], field: str
) -> tuple[set[str], bool]:
    values: set[str] = set()
    missing = False
    for record in records:
        value = record.metadata.get(field)
        if not isinstance(value, str) or not value.strip():
            missing = True
        else:
            values.add(value.strip())
    return values, missing


def build_release_report(
    contract: CoverageContract,
    evidence_store: EvidenceStore,
) -> ReleaseReport:
    blockers: set[str] = set()
    invalid_ids: set[str] = set()
    if contract.invalid_items:
        blockers.add("invalid_coverage_contract")

    try:
        records = evidence_store.records()
    except EvidenceManifestError:
        records = ()
        blockers.add("invalid_evidence_manifest")

    required = set(contract.required_evidence_ids)
    valid_passed: set[str] = set()
    for record in records:
        evidence_id = _record_key(record)
        artifact_valid = _artifact_is_valid(evidence_store, record)
        if not artifact_valid:
            blockers.add("artifact_hash_mismatch")
            invalid_ids.add(evidence_id)
        if record.metadata.get("status") != "passed":
            blockers.add("evidence_not_passed")
            invalid_ids.add(evidence_id)
        if artifact_valid and record.metadata.get("status") == "passed":
            valid_passed.add(evidence_id)

        provider = str(record.metadata.get("provider", "")).casefold()
        if provider == "fake" or _as_count(record.metadata.get("fake_count")) > 0:
            blockers.add("fake_provider")
        if record.metadata.get("_security_findings") or _as_count(
            record.metadata.get("api_key_leak_count")
        ) > 0:
            blockers.add("secret_material")
        if _as_count(record.metadata.get("template_fallback_count")) > 0:
            blockers.add("template_fallback")
        if _as_count(record.metadata.get("silent_fallback_count")) > 0:
            blockers.add("silent_fallback")
        if _as_count(record.metadata.get("partial_commit_count")) > 0:
            blockers.add("partial_state_commit")
        if _as_count(record.metadata.get("unattributed_console_errors")) > 0:
            blockers.add("unattributed_console_error")
        failed_calls = record.metadata.get("failed_calls", 0)
        if not _failed_calls_are_recovered(failed_calls, record.metadata):
            blockers.add("unrecovered_failed_call")

    missing_ids = tuple(sorted(required - valid_passed))
    if missing_ids:
        blockers.add("missing_evidence")

    provenance: dict[str, str] = {}
    for field, mixed_blocker in (
        ("run_id", "mixed_run_id"),
        ("git_commit", "mixed_git_commit"),
        ("v3_content_hash", "mixed_v3_content_hash"),
    ):
        values, missing = _provenance_values(records, field)
        if missing or (records and not values):
            blockers.add("missing_provenance")
        if len(values) > 1:
            blockers.add(mixed_blocker)
        if len(values) == 1:
            provenance[field] = next(iter(values))

    sorted_blockers = tuple(sorted(blockers))
    return ReleaseReport(
        publishable=not sorted_blockers,
        blockers=sorted_blockers,
        required_evidence=len(required),
        covered_evidence=len(required & valid_passed),
        missing_evidence_ids=missing_ids,
        invalid_evidence_ids=tuple(sorted(invalid_ids)),
        provenance=provenance,
    )
