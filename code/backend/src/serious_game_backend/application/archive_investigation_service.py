from __future__ import annotations

from serious_game_backend.domain.game_session import GameSession
from serious_game_backend.domain.gameplay_governance import ArchiveRecord
from serious_game_backend.domain.script_package import (
    ArchiveInvestigationDefinition,
    ScriptPackage,
)


def archive_definition(
    package: ScriptPackage,
    archive_id: str,
) -> ArchiveInvestigationDefinition | None:
    return next(
        (
            item
            for item in package.archive_investigations
            if item.archive_id == archive_id
        ),
        None,
    )


def first_read_cost(session: GameSession, package: ScriptPackage) -> int:
    tier = package.action_cost_tier(session.game_state.story_day).value
    return 1 if tier == "normal" else 2


def eligible_definitions(
    session: GameSession,
    package: ScriptPackage,
    *,
    unread_only: bool = False,
) -> tuple[ArchiveInvestigationDefinition, ...]:
    result = []
    for item in package.archive_investigations:
        if item.unlock_day > session.game_state.story_day:
            continue
        record = session.archive_records.get(item.archive_id)
        if unread_only and record is not None and record.read_at_days:
            continue
        result.append(item)
    return tuple(result)


def public_investigation_choice(
    session: GameSession,
    package: ScriptPackage,
    item: ArchiveInvestigationDefinition,
) -> dict:
    record = session.archive_records.get(item.archive_id)
    is_read = bool(record is not None and record.read_at_days)
    return {
        "target_id": item.archive_id,
        "label": item.title,
        "archive_id": item.archive_id,
        "title": item.title,
        "category": item.category,
        "evidence_level": item.evidence_level,
        "confidentiality": item.confidentiality,
        "first_read_cost_action_points": first_read_cost(session, package),
        "read_status": "read" if is_read else "unread",
        "result_fact_count": len(item.result_fact_ids),
        "strategic_uses": list(item.strategic_uses),
    }


def materialize_for_read(
    session: GameSession,
    item: ArchiveInvestigationDefinition,
) -> ArchiveRecord:
    record = session.archive_records.get(item.archive_id)
    if record is None:
        record = ArchiveRecord(
            archive_id=item.archive_id,
            category=item.category,
            title=item.title,
            content=item.content,
            source_type="archive_investigation",
            source_id=item.archive_id,
            acquired_day=item.unlock_day,
            acquired_via=f"story_day_unlock:D{item.unlock_day}",
            evidence_level=item.evidence_level,
            confidentiality=item.confidentiality,
        )
        session.archive_records[item.archive_id] = record
    else:
        record.category = item.category
        record.title = item.title
        record.evidence_level = item.evidence_level
        record.confidentiality = item.confidentiality
    return record
