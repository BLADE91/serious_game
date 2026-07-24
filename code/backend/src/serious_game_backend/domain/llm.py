from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class RoleTurnContext:
    session_id: str
    npc_id: str
    player_text: str
    story_day: int
    opportunity_id: str
    account_id: str = ""
    operation_id: str = ""
    allowed_fact_ids: tuple[str, ...] = ()
    required_disclosure_ids: tuple[str, ...] = ()
    npc_name: str = ""
    npc_state_tier: str = "deep"
    role_setting: str = ""
    prompt_template: str = ""
    prompt_version: str = "role-turn-v2"
    allowed_fact_texts: dict[str, str] = field(default_factory=dict)
    allowed_fact_markers: dict[str, tuple[str, ...]] = field(default_factory=dict)
    forbidden_fact_markers: tuple[str, ...] = ()
    memory_items: tuple[str, ...] = ()
    conversation_turn_count: int = 0
    conversation_history: tuple[dict[str, str], ...] = ()
    conversation_opening: str = ""
    conversation_goal: str = ""
    visible_world_context: dict = field(default_factory=dict)
    player_reference_materials: dict = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RoleTurnResult:
    npc_id: str
    dialogue: str
    portrait_state: str = "neutral"
    attitude_direction: str = "none"
    attitude_band: str = "none"
    anxiety_direction: str = "none"
    anxiety_band: str = "none"
    disclosure_id: str | None = None
    flag_candidates: tuple[str, ...] = ()
    will_share_with: tuple[str, ...] = ()
    memory_candidate: str | None = None
    risk_notes: tuple[str, ...] = ()
    conversation_state: str = "continue"
    exit_narrative: str | None = None


@dataclass(frozen=True, slots=True)
class ValidatedRoleTurn:
    npc_id: str
    dialogue: str
    portrait_state: str
    attitude_delta: int
    anxiety_delta: int
    disclosure_id: str | None = None
    memory_candidate: str | None = None
    will_share_with: tuple[str, ...] = ()
    risk_notes: tuple[str, ...] = ()
    conversation_state: str = "continue"
    exit_narrative: str | None = None
