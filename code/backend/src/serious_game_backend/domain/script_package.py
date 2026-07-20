from __future__ import annotations

from dataclasses import dataclass

from serious_game_backend.domain.action import ActionRule
from serious_game_backend.domain.enums import ActionCostTier, NPCStateTier
from serious_game_backend.domain.interaction_opportunity import InteractionOpportunity
from serious_game_backend.domain.story import (
    DecisionDefinition,
    FactDefinition,
    OriginDefinition,
    StoryDayDefinition,
)


@dataclass(frozen=True, slots=True)
class CalendarSegment:
    day_start: int
    day_end: int
    chapter: int
    cost_tier: ActionCostTier

    def contains(self, story_day: int) -> bool:
        return self.day_start <= story_day <= self.day_end


@dataclass(frozen=True, slots=True)
class FixedEventRule:
    event_id: str
    story_day: int
    title: str
    visible_summary: str
    trigger_type: str = "fixed"
    required_flags: frozenset[str] = frozenset()
    required_any_flags: frozenset[str] = frozenset()
    forbidden_flags: frozenset[str] = frozenset()
    forbidden_event_ids: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class MapLocationDefinition:
    location_id: str
    name: str
    description: str
    unlock_day: int
    linked_opportunity_ids: tuple[str, ...] = ()
    linked_event_ids: tuple[str, ...] = ()
    required_flags: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class MainEndingDefinition:
    ending_id: str
    order: int
    name: str
    tone: str
    condition: dict
    free_axis: str
    sub_ending_ids: tuple[str, ...]
    text: str = ""
    source_line: int = 0


@dataclass(frozen=True, slots=True)
class SubEndingDefinition:
    sub_ending_id: str
    main_ending_id: str
    axis: str
    axis_value: str
    title: str
    text: str = ""
    source_line: int = 0


@dataclass(frozen=True, slots=True)
class EndingAppendixDefinition:
    appendix_id: str
    title: str
    source: str


@dataclass(frozen=True, slots=True)
class ContentCatalogEntry:
    content_id: str
    chapter: int
    source_line: int


@dataclass(frozen=True, slots=True)
class MetricBand:
    minimum: int
    maximum: int
    label: str


@dataclass(frozen=True, slots=True)
class NPCProfileStub:
    npc_id: str
    name: str
    state_tier: NPCStateTier
    profile_id: str | None = None
    initial_attitude: int = 50
    initial_anxiety: int = 50
    role_setting: str = ""
    source_line: int = 0


@dataclass(frozen=True, slots=True)
class ScriptPackage:
    package_id: str
    package_version: str
    content_hash: str
    status: str
    title: str
    action_rules: dict[str, ActionRule]
    calendar_segments: tuple[CalendarSegment, ...]
    fixed_events: tuple[FixedEventRule, ...]
    metric_bands: dict[str, tuple[MetricBand, ...]]
    npc_profiles: tuple[NPCProfileStub, ...]
    story_days: dict[int, StoryDayDefinition]
    decisions: dict[str, DecisionDefinition]
    origins: dict[str, OriginDefinition]
    facts: dict[str, FactDefinition]
    interaction_opportunities: tuple[InteractionOpportunity, ...] = ()
    registered_flags: frozenset[str] = frozenset()
    map_locations: tuple[MapLocationDefinition, ...] = ()
    main_endings: tuple[MainEndingDefinition, ...] = ()
    sub_endings: tuple[SubEndingDefinition, ...] = ()
    ending_appendices: tuple[EndingAppendixDefinition, ...] = ()
    decision_catalog: tuple[ContentCatalogEntry, ...] = ()
    event_catalog: tuple[ContentCatalogEntry, ...] = ()
    source_sha256: str = ""
    role_turn_prompt: str = ""
    role_turn_prompt_version: str = "role-turn-v2"

    def action_cost_tier(self, story_day: int) -> ActionCostTier:
        matches = [item for item in self.calendar_segments if item.contains(story_day)]
        if len(matches) != 1:
            raise ValueError(f"story day {story_day} must match exactly one calendar segment")
        return matches[0].cost_tier

    def chapter_for(self, story_day: int) -> int:
        matches = [item for item in self.calendar_segments if item.contains(story_day)]
        if len(matches) != 1:
            raise ValueError(f"story day {story_day} must match exactly one calendar segment")
        return matches[0].chapter

    def story_day(self, story_day: int) -> StoryDayDefinition | None:
        return self.story_days.get(story_day)
