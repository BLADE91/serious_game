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
    transcript: list[dict[str, str]] = field(default_factory=list)
    started_at: str = field(default_factory=conversation_now_iso)

    def add_turn(self, player_text: str, npc_text: str) -> None:
        self.transcript.extend((
            {"speaker": "player", "text": player_text},
            {"speaker": "npc", "text": npc_text},
        ))
        self.turn_count += 1
