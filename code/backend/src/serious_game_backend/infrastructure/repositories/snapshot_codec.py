from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import secrets

from serious_game_backend.domain.game_session import GameSession
from serious_game_backend.domain.snapshots import GameSnapshot
from serious_game_backend.infrastructure.repositories.codec import (
    dumps,
    encode_session,
)


def build_snapshot(
    session: GameSession,
    *,
    snapshot_type: str,
    reason: str,
    parent_snapshot_id: str | None = None,
) -> GameSnapshot:
    payload = encode_session(session)
    canonical = dumps(payload)
    return GameSnapshot(
        snapshot_id=f"snap_{secrets.token_hex(16)}",
        session_id=session.session_id,
        account_id=session.account_id,
        timeline_id=session.timeline_id or f"timeline_{session.session_id}",
        snapshot_type=snapshot_type,
        reason=reason,
        story_day=session.game_state.story_day,
        state_version=session.state_version,
        package_id=session.package_id,
        package_version=session.package_version,
        package_content_hash=session.package_content_hash,
        snapshot_hash="sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        parent_snapshot_id=parent_snapshot_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        session_payload=payload,
    )


def verify_snapshot(snapshot: GameSnapshot) -> bool:
    canonical = dumps(snapshot.session_payload)
    digest = "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return secrets.compare_digest(digest, snapshot.snapshot_hash)
