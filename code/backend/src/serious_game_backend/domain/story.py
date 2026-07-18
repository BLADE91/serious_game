from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class NarrativeBlock:
    block_id: str
    kind: str
    text: str
    speaker: str | None = None
    origin_ids: frozenset[str] = frozenset()
    required_flags: frozenset[str] = frozenset()
    required_any_flags: frozenset[str] = frozenset()
    forbidden_flags: frozenset[str] = frozenset()

    def is_visible(self, *, origin_id: str, flags: set[str]) -> bool:
        return (
            (not self.origin_ids or origin_id in self.origin_ids)
            and self.required_flags.issubset(flags)
            and (not self.required_any_flags or bool(self.required_any_flags & flags))
            and not bool(self.forbidden_flags & flags)
        )


@dataclass(frozen=True, slots=True)
class OriginDefinition:
    origin_id: str
    title: str
    description: str


@dataclass(frozen=True, slots=True)
class FactDefinition:
    fact_id: str
    title: str
    text: str
    category: str = "fact"
    source_line: int = 0


@dataclass(frozen=True, slots=True)
class ScriptedEffects:
    metric_deltas: dict[str, tuple[int, int]] = field(default_factory=dict)
    ledger_deltas: dict[str, tuple[int, int]] = field(default_factory=dict)
    open_flags: frozenset[str] = frozenset()
    close_flags: frozenset[str] = frozenset()
    state_assignments: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConditionalEffectDefinition:
    effects: ScriptedEffects
    replace_base: bool = False
    required_flags: frozenset[str] = frozenset()
    required_any_flags: frozenset[str] = frozenset()
    forbidden_flags: frozenset[str] = frozenset()
    required_state_values: dict[str, str] = field(default_factory=dict)
    forbidden_state_values: dict[str, frozenset[str]] = field(default_factory=dict)
    minimum_ledger_values: dict[str, int] = field(default_factory=dict)

    def matches(
        self,
        flags: set[str],
        state_values: dict[str, str],
        ledger_values: dict[str, int] | None = None,
    ) -> bool:
        ledger_values = ledger_values or {}
        return (
            self.required_flags.issubset(flags)
            and (not self.required_any_flags or bool(self.required_any_flags & flags))
            and not bool(self.forbidden_flags & flags)
            and all(state_values.get(key) == value for key, value in self.required_state_values.items())
            and all(state_values.get(key) not in values for key, values in self.forbidden_state_values.items())
            and all(
                ledger_values.get(key, 0) >= minimum
                for key, minimum in self.minimum_ledger_values.items()
            )
        )


@dataclass(frozen=True, slots=True)
class AvailabilityClause:
    required_flags: frozenset[str] = frozenset()
    required_any_flags: frozenset[str] = frozenset()
    forbidden_flags: frozenset[str] = frozenset()
    required_state_values: dict[str, str] = field(default_factory=dict)
    forbidden_state_values: dict[str, frozenset[str]] = field(default_factory=dict)
    minimum_ledger_values: dict[str, int] = field(default_factory=dict)
    maximum_ledger_values: dict[str, int] = field(default_factory=dict)

    def matches(
        self,
        flags: set[str],
        state_values: dict[str, str],
        ledger_values: dict[str, int] | None = None,
    ) -> bool:
        ledger_values = ledger_values or {}
        return (
            self.required_flags.issubset(flags)
            and (not self.required_any_flags or bool(self.required_any_flags & flags))
            and not bool(self.forbidden_flags & flags)
            and all(state_values.get(key) == value for key, value in self.required_state_values.items())
            and all(state_values.get(key) not in values for key, values in self.forbidden_state_values.items())
            and all(ledger_values.get(key, 0) >= value for key, value in self.minimum_ledger_values.items())
            and all(ledger_values.get(key, 0) <= value for key, value in self.maximum_ledger_values.items())
        )


@dataclass(frozen=True, slots=True)
class DecisionOptionDefinition:
    option_id: str
    text: str
    consequence: str
    effects: ScriptedEffects = field(default_factory=ScriptedEffects)
    required_flags: frozenset[str] = frozenset()
    required_any_flags: frozenset[str] = frozenset()
    forbidden_flags: frozenset[str] = frozenset()
    required_state_values: dict[str, str] = field(default_factory=dict)
    forbidden_state_values: dict[str, frozenset[str]] = field(default_factory=dict)
    minimum_ledger_values: dict[str, int] = field(default_factory=dict)
    maximum_ledger_values: dict[str, int] = field(default_factory=dict)
    availability_any: tuple[AvailabilityClause, ...] = ()
    unavailable_reason: str = "条件不足"
    conditional_effects: tuple[ConditionalEffectDefinition, ...] = ()

    def is_available(
        self,
        flags: set[str],
        state_values: dict[str, str],
        ledger_values: dict[str, int] | None = None,
    ) -> bool:
        ledger_values = ledger_values or {}
        return (
            self.required_flags.issubset(flags)
            and (not self.required_any_flags or bool(self.required_any_flags & flags))
            and not bool(self.forbidden_flags & flags)
            and all(state_values.get(key) == value for key, value in self.required_state_values.items())
            and all(state_values.get(key) not in values for key, values in self.forbidden_state_values.items())
            and all(ledger_values.get(key, 0) >= value for key, value in self.minimum_ledger_values.items())
            and all(ledger_values.get(key, 0) <= value for key, value in self.maximum_ledger_values.items())
            and (
                not self.availability_any
                or any(item.matches(flags, state_values, ledger_values) for item in self.availability_any)
            )
        )


@dataclass(frozen=True, slots=True)
class DecisionDefinition:
    decision_id: str
    story_day: int
    title: str
    prompt: str
    options: tuple[DecisionOptionDefinition, ...]
    followup_blocks: tuple[NarrativeBlock, ...] = ()
    action_point_cost: int = 0
    skippable: bool = False
    input_kind: str = "choice"
    input_schema: dict = field(default_factory=dict)
    required_flags: frozenset[str] = frozenset()
    required_any_flags: frozenset[str] = frozenset()
    forbidden_flags: frozenset[str] = frozenset()
    early_day: int | None = None
    early_required_flags: frozenset[str] = frozenset()
    presentation_blocks: tuple[NarrativeBlock, ...] = ()

    def option(self, option_id: str) -> DecisionOptionDefinition | None:
        return next((item for item in self.options if item.option_id == option_id), None)

    def is_available(self, flags: set[str]) -> bool:
        return (
            self.required_flags.issubset(flags)
            and (not self.required_any_flags or bool(self.required_any_flags & flags))
            and not bool(self.forbidden_flags & flags)
        )

    def is_due_early(self, story_day: int, flags: set[str]) -> bool:
        return (
            self.early_day == story_day
            and bool(self.early_required_flags)
            and self.early_required_flags.issubset(flags)
        )


@dataclass(frozen=True, slots=True)
class StoryDayDefinition:
    beat_id: str
    story_day: int
    chapter: int
    day_mode: str
    title: str
    allow_actions: bool = True
    allow_end_day: bool = True
    end_day_requires_flags: frozenset[str] = frozenset()
    opening_blocks: tuple[NarrativeBlock, ...] = ()
    opening_decision_id: str | None = None
    decision_ids: tuple[str, ...] = ()
    night_blocks: tuple[NarrativeBlock, ...] = ()
    night_effects: ScriptedEffects = field(default_factory=ScriptedEffects)
    night_conditional_effects: tuple[ConditionalEffectDefinition, ...] = ()


@dataclass(frozen=True, slots=True)
class VisibleNarrativeEntry:
    cursor: int
    story_day: int
    kind: str
    text: str
    speaker: str | None = None
