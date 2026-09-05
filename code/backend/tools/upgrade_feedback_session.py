"""Explicit, backed-up SQLite progress upgrade. Stop that database's server first.

No implicit content-lock bypass. Previously emitted story remains historical;
newly emitted content uses the reviewed revision. Financial/gameplay state and
existing snapshots are not rewritten. Memory and MySQL stores are not supported.
"""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from serious_game_backend.infrastructure.script_packages.file_loader import FileScriptPackageLoader
from serious_game_backend.infrastructure.repositories.codec import decode_session
from serious_game_backend.infrastructure.repositories.sqlite import SqliteRuntimeStore, SqliteSnapshotRepository

OLD_VERSION = "3.5.12-map-coverage"
OLD_HASH = "sha256:008d3dfaef84a7f5314a648832e344ff11e25949a8f7d8f1758f5d8e4425318c"
NEW_VERSION = "3.5.13-feedback-reading-actions"


def upgrade_record(session, package):
    if (session.package_id, session.package_version, session.package_content_hash) != (
            "pkg_gameplay_v3", OLD_VERSION, OLD_HASH):
        raise ValueError("This progress does not match the reviewed source revision")
    if package.package_id != "pkg_gameplay_v3" or package.package_version != NEW_VERSION:
        raise ValueError("Unexpected target content revision")
    if session.processing_action_id:
        raise ValueError("An operation is still in progress; finish it before upgrading")
    session.package_version = package.package_version
    session.package_content_hash = package.content_hash
    if session.pending_decision:
        d = package.decisions[session.pending_decision.decision_id]
        visible = [b for b in d.presentation_blocks if b.is_visible(origin_id=session.origin_id, flags=session.flags)]
        if visible:
            session.pending_decision = replace(session.pending_decision, scene_id=visible[-1].scene_id)
    session.logs.append({"type": "reviewed_content_upgrade", "from_version": OLD_VERSION,
        "from_hash": OLD_HASH, "to_version": package.package_version,
        "to_hash": package.content_hash, "story_day": session.game_state.story_day,
        "visible_to_player": False})
    session.state_version += 1
    session.touch()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--apply", action="store_true", help="Default is a read-only validation")
    args = parser.parse_args()
    db = args.database.resolve(strict=True)
    package = FileScriptPackageLoader().load(ROOT / "content/packages/pkg_gameplay_v3")
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as source:
        row = source.execute("select payload_json from runtime_game_sessions where session_id=?", (args.session_id,)).fetchone()
        if not row:
            raise ValueError("Session not found")
        session = decode_session(json.loads(row[0]))
        version = session.state_version
        upgrade_record(session, package)
        if not args.apply:
            print("Validated. Stop the server using this database, then run with --apply.")
            return
        backup = db.with_name(db.name + ".before-feedback-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".bak")
        if backup.exists():
            raise ValueError("Backup target already exists")
        with sqlite3.connect(backup) as destination:
            source.backup(destination)
    # The repository performs a version-checked write and publishes a new
    # automatic snapshot in the same transaction. Old snapshots stay unchanged.
    snapshots = SqliteSnapshotRepository(SqliteRuntimeStore(db))
    snapshots.commit_session_snapshot(session, expected_version=version,
        snapshot_type="auto", reason="reviewed_feedback_content_upgrade")
    print("Upgraded selected progress; backup:", backup)


if __name__ == "__main__":
    main()
