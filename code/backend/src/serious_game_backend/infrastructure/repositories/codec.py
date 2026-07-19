from __future__ import annotations

from dataclasses import asdict
import json

from serious_game_backend.domain.enums import (
    AvailabilityMode,
    DecisionState,
    NPCStateTier,
    OperationStatus,
    SessionStatus,
)
from serious_game_backend.domain.events import (
    PendingDecision,
    VisibleDecisionOption,
    VisibleEvent,
)
from serious_game_backend.domain.game_session import GameSession
from serious_game_backend.domain.game_state import GameState
from serious_game_backend.domain.npc_state import NPCState
from serious_game_backend.domain.operation import OperationRecord
from serious_game_backend.domain.story import VisibleNarrativeEntry


def dumps(value: dict) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def encode_session(session: GameSession) -> dict:
    pending = None
    if session.pending_decision is not None:
        pending = {
            "event_instance_id": session.pending_decision.event_instance_id,
            "decision_id": session.pending_decision.decision_id,
            "option_ids": list(session.pending_decision.option_ids),
            "state": session.pending_decision.state.value,
            "presented_state_version": (
                session.pending_decision.presented_state_version
            ),
            "visible_title": session.pending_decision.visible_title,
            "visible_text": session.pending_decision.visible_text,
            "options": [asdict(item) for item in session.pending_decision.options],
            "input_kind": session.pending_decision.input_kind,
            "input_schema": session.pending_decision.input_schema,
            "context": session.pending_decision.context,
        }
    return {
        "schema_version": 2,
        "session_id": session.session_id,
        "account_id": session.account_id,
        "package_id": session.package_id,
        "package_version": session.package_version,
        "package_content_hash": session.package_content_hash,
        "random_seed": session.random_seed,
        "origin_id": session.origin_id,
        "environment": session.environment,
        "consent_record_id": session.consent_record_id,
        "research_subject_id": session.research_subject_id,
        "experiment_id": session.experiment_id,
        "experiment_group_id": session.experiment_group_id,
        "game_state": asdict(session.game_state),
        "npc_states": {
            npc_id: {
                "npc_id": item.npc_id,
                "state_tier": item.state_tier.value,
                "availability_mode": item.availability_mode.value,
                "profile_id": item.profile_id,
                "trust_score": item.trust_score,
                "trust_locked": item.trust_locked,
                "trust_effects_applied": sorted(item.trust_effects_applied),
                "attitude_score": item.attitude_score,
                "anxiety_score": item.anxiety_score,
                "memory_id": item.memory_id,
                "chapter_disclosure_used": item.chapter_disclosure_used,
                "known_fact_ids": sorted(item.known_fact_ids),
                "owned_evidence_ids": sorted(item.owned_evidence_ids),
                "special_flags": sorted(item.special_flags),
            }
            for npc_id, item in session.npc_states.items()
        },
        "status": session.status.value,
        "state_version": session.state_version,
        "processing_action_id": session.processing_action_id,
        "pending_decision": pending,
        "pending_decision_queue": list(session.pending_decision_queue),
        "flags": sorted(session.flags),
        "state_values": session.state_values,
        "triggered_events": sorted(session.triggered_events),
        "visible_events": [asdict(item) for item in session.visible_events],
        "story_beat_id": session.story_beat_id,
        "narrative_feed": [asdict(item) for item in session.narrative_feed],
        "next_feed_cursor": session.next_feed_cursor,
        "known_fact_ids": sorted(session.known_fact_ids),
        "night_logs": session.night_logs,
        "ending_result": session.ending_result,
        "decision_parameters": session.decision_parameters,
        "logs": session.logs,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


def decode_session(value: dict) -> GameSession:
    pending_value = value.get("pending_decision")
    pending = None
    if pending_value is not None:
        pending = PendingDecision(
            event_instance_id=str(pending_value["event_instance_id"]),
            decision_id=str(pending_value["decision_id"]),
            option_ids=tuple(pending_value["option_ids"]),
            state=DecisionState(pending_value["state"]),
            presented_state_version=int(
                pending_value["presented_state_version"]
            ),
            visible_title=str(pending_value["visible_title"]),
            visible_text=str(pending_value["visible_text"]),
            options=tuple(
                VisibleDecisionOption(**item)
                for item in pending_value.get("options", [])
            ),
            input_kind=str(pending_value.get("input_kind", "choice")),
            input_schema=pending_value.get("input_schema"),
            context=dict(pending_value.get("context", {})),
        )
    return GameSession(
        session_id=str(value["session_id"]),
        account_id=str(value["account_id"]),
        package_id=str(value["package_id"]),
        package_version=str(value["package_version"]),
        package_content_hash=str(value["package_content_hash"]),
        random_seed=str(value["random_seed"]),
        game_state=GameState(**value["game_state"]),
        origin_id=str(value["origin_id"]),
        environment=str(value.get("environment", "sandbox")),
        consent_record_id=value.get("consent_record_id"),
        research_subject_id=value.get("research_subject_id"),
        experiment_id=value.get("experiment_id"),
        experiment_group_id=value.get("experiment_group_id"),
        npc_states={
            npc_id: NPCState(
                npc_id=str(item["npc_id"]),
                state_tier=NPCStateTier(item["state_tier"]),
                availability_mode=AvailabilityMode(item["availability_mode"]),
                profile_id=item.get("profile_id"),
                trust_score=item.get("trust_score"),
                trust_locked=bool(item.get("trust_locked", False)),
                trust_effects_applied=frozenset(
                    item.get("trust_effects_applied", [])
                ),
                attitude_score=item.get("attitude_score"),
                anxiety_score=item.get("anxiety_score"),
                memory_id=item.get("memory_id"),
                chapter_disclosure_used=bool(
                    item.get("chapter_disclosure_used", False)
                ),
                known_fact_ids=frozenset(item.get("known_fact_ids", [])),
                owned_evidence_ids=frozenset(
                    item.get("owned_evidence_ids", [])
                ),
                special_flags=frozenset(item.get("special_flags", [])),
            )
            for npc_id, item in value.get("npc_states", {}).items()
        },
        status=SessionStatus(value["status"]),
        state_version=int(value["state_version"]),
        processing_action_id=value.get("processing_action_id"),
        pending_decision=pending,
        pending_decision_queue=list(value.get("pending_decision_queue", [])),
        flags=set(value.get("flags", [])),
        state_values=dict(
            value.get("state_values", {"lead_roster_disposition": "未获取"})
        ),
        triggered_events=set(value.get("triggered_events", [])),
        visible_events=[
            VisibleEvent(**item) for item in value.get("visible_events", [])
        ],
        story_beat_id=value.get("story_beat_id"),
        narrative_feed=[
            VisibleNarrativeEntry(**item)
            for item in value.get("narrative_feed", [])
        ],
        next_feed_cursor=int(value.get("next_feed_cursor", 1)),
        known_fact_ids=set(value.get("known_fact_ids", [])),
        night_logs=list(value.get("night_logs", [])),
        ending_result=value.get("ending_result"),
        decision_parameters=dict(value.get("decision_parameters", {})),
        logs=list(value.get("logs", [])),
        created_at=str(value["created_at"]),
        updated_at=str(value["updated_at"]),
    )


def encode_operation(operation: OperationRecord) -> dict:
    return {
        "operation_id": operation.operation_id,
        "account_id": operation.account_id,
        "session_id": operation.session_id,
        "client_action_id": operation.client_action_id,
        "request_hash": operation.request_hash,
        "status": operation.status.value,
        "attempt_count": operation.attempt_count,
        "response": operation.response,
        "error": operation.error,
        "created_at": operation.created_at,
        "updated_at": operation.updated_at,
    }


def decode_operation(value: dict) -> OperationRecord:
    return OperationRecord(
        operation_id=str(value["operation_id"]),
        account_id=str(value["account_id"]),
        session_id=value.get("session_id"),
        client_action_id=str(value["client_action_id"]),
        request_hash=str(value["request_hash"]),
        status=OperationStatus(value["status"]),
        attempt_count=int(value.get("attempt_count", 1)),
        response=value.get("response"),
        error=value.get("error"),
        created_at=str(value["created_at"]),
        updated_at=str(value["updated_at"]),
    )
