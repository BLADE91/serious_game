from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from serious_game_backend.domain.events import PendingDecision, VisibleEvent
from serious_game_backend.domain.game_state import GameState
from serious_game_backend.domain.npc_state import NPCState
from serious_game_backend.domain.enums import SessionStatus
from serious_game_backend.domain.story import VisibleNarrativeEntry
from serious_game_backend.domain.conversation import ActiveConversation


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class GameSession:
    session_id: str
    account_id: str
    package_id: str
    package_version: str
    package_content_hash: str
    random_seed: str
    game_state: GameState
    origin_id: str
    environment: str = "sandbox"
    consent_record_id: str | None = None
    research_subject_id: str | None = None
    experiment_id: str | None = None
    experiment_group_id: str | None = None
    npc_states: dict[str, NPCState] = field(default_factory=dict)
    status: SessionStatus = SessionStatus.ACTIVE
    state_version: int = 1
    processing_action_id: str | None = None
    pending_decision: PendingDecision | None = None
    pending_decision_queue: list[str] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)
    state_values: dict[str, str] = field(
        default_factory=lambda: {"lead_roster_disposition": "未获取"}
    )
    triggered_events: set[str] = field(default_factory=set)
    visible_events: list[VisibleEvent] = field(default_factory=list)
    story_beat_id: str | None = None
    narrative_feed: list[VisibleNarrativeEntry] = field(default_factory=list)
    next_feed_cursor: int = 1
    known_fact_ids: set[str] = field(default_factory=set)
    night_logs: list[dict] = field(default_factory=list)
    ending_result: dict | None = None
    decision_parameters: dict[str, dict] = field(default_factory=dict)
    active_conversation: ActiveConversation | None = None
    logs: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    def touch(self) -> None:
        self.updated_at = now_iso()

    def append_narrative(
        self,
        *,
        story_day: int,
        kind: str,
        text: str,
        speaker: str | None = None,
    ) -> None:
        self.narrative_feed.append(VisibleNarrativeEntry(
            cursor=self.next_feed_cursor,
            story_day=story_day,
            kind=kind,
            text=text,
            speaker=speaker,
        ))
        self.next_feed_cursor += 1
