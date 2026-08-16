from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def conversation_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class ActiveConversation:
    conversation_id: str
    opportunity_id: str
    npc_id: str
    story_day: int
    turn_count: int = 0
    quoted_cost: int = 0
    cost_charged: bool = False
    transcript: list[dict[str, str]] = field(default_factory=list)
    started_at: str = field(default_factory=conversation_now_iso)

    def add_turn(self, player_text: str, npc_text: str) -> None:
        self.transcript.extend((
            {"speaker": "player", "text": player_text},
            {"speaker": "npc", "text": npc_text},
        ))
        self.turn_count += 1


@dataclass(slots=True)
class ForcedGroupConversation:
    conversation_id: str
    conversation_type: str
    initiator_npc_id: str
    participant_ids: tuple[str, ...]
    agenda: str
    demands: tuple[str, ...]
    urgency: str
    story_day: int
    turn_count: int = 0
    max_turns: int = 3
    transcript: list[dict] = field(default_factory=list)
    status: str = "pending"
    started_at: str = field(default_factory=conversation_now_iso)

    def add_player_turn(self, text: str) -> None:
        self.transcript.append({"speaker_type": "player", "text": text})

    def add_npc_turn(
        self, *, npc_id: str, npc_name: str, model_id: str, text: str
    ) -> None:
        self.transcript.append({
            "speaker_type": "npc",
            "npc_id": npc_id,
            "npc_name": npc_name,
            "model_id": model_id,
            "text": text,
        })
