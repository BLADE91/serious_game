from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping


_SENSITIVE_KEY_PARTS = ("api_key", "authorization", "cookie", "secret", "token")
_SAFE_SECURITY_COUNTERS = {"api_key_leak_count"}
_SECRET_PATTERNS = (
    re.compile(rb"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(rb"(?i)authorization\s*[:=]\s*bearer\s+[A-Za-z0-9._-]{8,}"),
    re.compile(rb"(?i)(?:api[_-]?key|access[_-]?token)\s*[:=]\s*[A-Za-z0-9._-]{12,}"),
)


class SecretMaterialError(ValueError):
    """Raised before evidence containing credential-shaped text is recorded."""


class EvidenceManifestError(ValueError):
    """Raised when an existing append-only manifest cannot be parsed safely."""


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    coverage_id: str
    evidence_type: str
    artifact_path: str
    sha256: str
    recorded_at: str
    metadata: dict[str, Any]


def _contains_secret_material(payload: bytes) -> bool:
    return any(pattern.search(payload) is not None for pattern in _SECRET_PATTERNS)


def _redact(value: Any, findings: list[str], path: str = "metadata") -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}"
            folded_key = key.casefold()
            if (
                folded_key not in _SAFE_SECURITY_COUNTERS
                and any(part in folded_key for part in _SENSITIVE_KEY_PARTS)
            ):
                findings.append(f"sensitive_field:{child_path}")
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = _redact(child, findings, child_path)
        return redacted
    if isinstance(value, (list, tuple)):
        return [
            _redact(child, findings, f"{path}[{index}]")
            for index, child in enumerate(value)
        ]
    if isinstance(value, bytes):
        if _contains_secret_material(value):
            findings.append(f"secret_pattern:{path}")
            return "[REDACTED]"
        return value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        if _contains_secret_material(value.encode("utf-8")):
            findings.append(f"secret_pattern:{path}")
            return "[REDACTED]"
        return value
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


class EvidenceStore:
    """Append-only, content-addressed evidence manifest rooted in one run folder."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "manifest.jsonl"

    def _resolve_artifact(self, artifact_path: str | Path) -> tuple[Path, str]:
        relative = Path(artifact_path)
        if relative.is_absolute():
            raise ValueError("artifact_path must be relative to the evidence root")
        resolved = (self.root / relative).resolve()
        try:
            normalized = resolved.relative_to(self.root).as_posix()
        except ValueError as exc:
            raise ValueError("artifact_path escapes the evidence root") from exc
        if not resolved.is_file():
            raise FileNotFoundError(resolved)
        return resolved, normalized

    def record(
        self,
        coverage_id: str,
        evidence_type: str,
        artifact_path: str | Path,
        metadata: Mapping[str, Any],
    ) -> EvidenceRecord:
        if not coverage_id.strip() or not evidence_type.strip():
            raise ValueError("coverage_id and evidence_type are required")
        artifact, normalized_path = self._resolve_artifact(artifact_path)
        payload = artifact.read_bytes()
        if _contains_secret_material(payload):
            raise SecretMaterialError(
                f"evidence artifact contains credential-shaped text: {normalized_path}"
            )

        findings: list[str] = []
        safe_metadata = _redact(dict(metadata), findings)
        if findings:
            safe_metadata["_security_findings"] = sorted(set(findings))
        record = EvidenceRecord(
            coverage_id=coverage_id,
            evidence_type=evidence_type,
            artifact_path=normalized_path,
            sha256=sha256(payload).hexdigest(),
            recorded_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            metadata=safe_metadata,
        )
        with self.manifest_path.open("a", encoding="utf-8", newline="\n") as manifest:
            manifest.write(json.dumps(asdict(record), ensure_ascii=False, sort_keys=True))
            manifest.write("\n")
        return record

    def records(self) -> tuple[EvidenceRecord, ...]:
        if not self.manifest_path.exists():
            return ()
        records: list[EvidenceRecord] = []
        for line_number, line in enumerate(
            self.manifest_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                records.append(EvidenceRecord(
                    coverage_id=str(payload["coverage_id"]),
                    evidence_type=str(payload["evidence_type"]),
                    artifact_path=str(payload["artifact_path"]),
                    sha256=str(payload["sha256"]),
                    recorded_at=str(payload["recorded_at"]),
                    metadata=dict(payload["metadata"]),
                ))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise EvidenceManifestError(
                    f"invalid evidence manifest line {line_number}"
                ) from exc
        return tuple(records)
