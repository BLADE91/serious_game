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
    big_five: dict[str, int] = field(default_factory=dict)
    prompt_template: str = ""
    prompt_version: str = "role-turn-v2"
    allowed_fact_texts: dict[str, str] = field(default_factory=dict)
    allowed_fact_markers: dict[str, tuple[str, ...]] = field(default_factory=dict)
    forbidden_fact_markers: tuple[str, ...] = ()
    forbidden_fact_signatures: dict[str, tuple[str, ...]] = field(default_factory=dict)
    memory_items: tuple[str, ...] = ()
    relationship_context: dict[str, str] = field(default_factory=dict)
    recent_visible_change_reasons: tuple[str, ...] = ()
    unresolved_commitments: tuple[str, ...] = ()
    unresolved_demands: tuple[str, ...] = ()
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
    input_relevance: str = "relevant"
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
    input_relevance: str = "relevant"
    disclosure_id: str | None = None
    memory_candidate: str | None = None
    will_share_with: tuple[str, ...] = ()
    risk_notes: tuple[str, ...] = ()
    conversation_state: str = "continue"
    exit_narrative: str | None = None


@dataclass(frozen=True, slots=True)
class NightAgentContext:
    session_id: str
    account_id: str
    operation_id: str
    story_day: int
    scene_id: str
    phase: str
    npc_id: str
    npc_name: str
    role_setting: str
    big_five: dict[str, int]
    counterpart_ids: tuple[str, ...]
    transcript: tuple[dict[str, str], ...] = ()
    round_index: int = 0
    scene_goal: str = ""
    private_context: str = ""
    allowed_actions: tuple[dict, ...] = ()
    allowed_topics: tuple[str, ...] = ()
    forbidden_disclosure_markers: tuple[str, ...] = ()
    max_contacts: int = 0
    player_text: str = ""
    allowed_followup_type: str = ""
    model_id: str = ""
    prompt_version: str = "night-agent-v1"


@dataclass(frozen=True, slots=True)
class NightAgentResult:
    npc_id: str
    model_id: str
    dialogue: str | None = None
    action_id: str | None = None
    contact_ids: tuple[str, ...] = ()
    contact_response: str | None = None
    initiate_followup: bool = False
    followup_type: str | None = None
    participant_ids: tuple[str, ...] = ()
    agenda: str = ""
    demands: tuple[str, ...] = ()
    urgency: str = "none"
    target_ids: tuple[str, ...] = ()
    topic_ids: tuple[str, ...] = ()
    rationale: str = ""


@dataclass(frozen=True, slots=True)
class GovernanceLLMContext:
    session_id: str
    account_id: str
    operation_id: str
    story_day: int
    task: str
    actor_id: str
    actor_name: str
    actor_profile: str
    payload: dict
    prompt_version: str = "governance-workflow-v1"


@dataclass(frozen=True, slots=True)
class GovernanceLLMResult:
    task: str
    data: dict
    model_id: str
