"""Back up and migrate one offline SQLite progress record; default is dry-run."""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from serious_game_backend.application.contract_accounting import migrate_contract_accounting
from serious_game_backend.infrastructure.repositories.codec import decode_session
from serious_game_backend.infrastructure.repositories.sqlite import SqliteRuntimeStore, SqliteSnapshotRepository


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db = args.database.resolve(strict=True)
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as source:
        row = source.execute("select payload_json from runtime_game_sessions where session_id=?",
                             (args.session_id,)).fetchone()
        if row is None:
            raise ValueError("Session not found")
        session = decode_session(json.loads(row[0]))
        if session.processing_action_id:
            raise ValueError("An operation is in progress; finish it before migration")
        version = session.state_version
        before = session.game_state.budget_remaining
        changed = migrate_contract_accounting(session)
        print(json.dumps({"changed": changed, "cash_before": before,
                          "cash_after": session.game_state.budget_remaining}, ensure_ascii=False))
        if not args.apply or not changed:
            return
        backup = db.with_name(db.name + ".before-contract-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".bak")
        with sqlite3.connect(backup) as destination:
            source.backup(destination)
    session.state_version += 1
    session.touch()
    SqliteSnapshotRepository(SqliteRuntimeStore(db)).commit_session_snapshot(
        session, expected_version=version, snapshot_type="auto", reason="contract_accounting_migration")
    print("Migrated; backup:", backup)


if __name__ == "__main__":
    main()
