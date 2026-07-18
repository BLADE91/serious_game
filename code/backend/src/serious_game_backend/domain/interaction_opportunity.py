from __future__ import annotations

from dataclasses import dataclass, field

from serious_game_backend.domain.enums import AvailabilityMode
from serious_game_backend.domain.story import NarrativeBlock, ScriptedEffects


@dataclass(frozen=True, slots=True)
class InteractionOpportunity:
    opportunity_id: str
    npc_id: str
    entry_type: str
    day_min: int
    day_max: int
    action_id: str
    availability_mode: AvailabilityMode = AvailabilityMode.FREE
    requires_flags: frozenset[str] = frozenset()
    requires_events: frozenset[str] = frozenset()
    closes_on_flags: frozenset[str] = frozenset()
    allowed_fact_ids: tuple[str, ...] = ()
    completion_flags: frozenset[str] = frozenset()
    completion_fact_ids: frozenset[str] = frozenset()
    completion_blocks: tuple[NarrativeBlock, ...] = ()
    completion_effects: ScriptedEffects = field(default_factory=ScriptedEffects)
    completion_decision_id: str | None = None

    def __post_init__(self) -> None:
        if not self.opportunity_id or not self.npc_id or not self.action_id:
            raise ValueError("opportunity_id, npc_id, and action_id are required")
        if self.day_min < 1 or self.day_max > 90 or self.day_min > self.day_max:
            raise ValueError("opportunity day range is invalid")
