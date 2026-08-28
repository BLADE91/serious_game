from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Protocol

from serious_game_backend.domain.script_package import ScriptPackage


FORBIDDEN_ROUTE_KEYS = frozenset({
    "state_patch",
    "flags_override",
    "metric_override",
    "database_operations",
    "database_operation",
    "sql",
})


@dataclass(frozen=True, slots=True)
class EndingWitness:
    route_id: str
    target_main_ending_ids: tuple[str, ...]
    target_sub_ending_ids: tuple[str, ...]
    origin_id: str
    decision_policy: dict[str, Any]
    daily_action_policy: tuple[dict[str, Any], ...]
    conversation_strategies: dict[str, Any]
    expected_end_day: int
    actual_main_ending_id: str | None = None
    actual_sub_ending_id: str | None = None
    semantic_state_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WitnessCoverage:
    main_ending_ids: frozenset[str]
    sub_ending_ids: frozenset[str]
    invalid_state_patches: tuple[str, ...]
    invalid_targets: tuple[str, ...]
    duplicate_route_ids: tuple[str, ...]

    @property
    def is_complete(self) -> bool:
        return not (
            self.invalid_state_patches
            or self.invalid_targets
            or self.duplicate_route_ids
        )


class RouteDriver(Protocol):
    profiles: Iterable[EndingWitness]

    def run(self, profile: EndingWitness) -> dict[str, Any]: ...


def semantic_state_hash(state: dict[str, Any]) -> str:
    """Hash only stable player-visible/authoritative route semantics."""

    allowed = {
        "story_day",
        "metrics",
        "ledger",
        "flags",
        "known_facts",
        "contracts",
        "documents",
        "npc_memories",
        "pending_decision",
        "active_group_conversation",
    }
    payload = {key: state[key] for key in sorted(allowed & state.keys())}
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _walk_forbidden(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if str(key).lower() in FORBIDDEN_ROUTE_KEYS:
                found.append(child_path)
            found.extend(_walk_forbidden(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_walk_forbidden(child, f"{path}[{index}]"))
    return found


def _as_witness(
    document: dict[str, Any],
    *,
    index: int,
    decision_policy_templates: dict[str, dict[str, Any]],
    main_ending_policy_overrides: dict[str, dict[str, Any]],
    sub_ending_policy_overrides: dict[str, dict[str, Any]],
) -> EndingWitness:
    required = {
        "route_id",
        "target_main_ending_ids",
        "target_sub_ending_ids",
        "origin_id",
        "decision_policy",
        "daily_action_policy",
        "conversation_strategies",
        "expected_end_day",
    }
    missing = sorted(required - document.keys())
    if missing:
        raise ValueError(f"profile {index} missing fields: {', '.join(missing)}")
    template_id = document.get("decision_policy_template_id")
    if template_id is None:
        decision_policy: dict[str, Any] = {}
    else:
        if str(template_id) not in decision_policy_templates:
            raise ValueError(
                f"profile {index} references unknown decision policy template: "
                f"{template_id}"
            )
        decision_policy = dict(decision_policy_templates[str(template_id)])
    decision_policy.update(dict(document["decision_policy"]))
    for main_ending_id in document["target_main_ending_ids"]:
        decision_policy.update(
            main_ending_policy_overrides.get(str(main_ending_id), {})
        )
    for sub_ending_id in document["target_sub_ending_ids"]:
        decision_policy.update(
            sub_ending_policy_overrides.get(str(sub_ending_id), {})
        )
    return EndingWitness(
        route_id=str(document["route_id"]),
        target_main_ending_ids=tuple(map(str, document["target_main_ending_ids"])),
        target_sub_ending_ids=tuple(map(str, document["target_sub_ending_ids"])),
        origin_id=str(document["origin_id"]),
        decision_policy=decision_policy,
        daily_action_policy=tuple(dict(item) for item in document["daily_action_policy"]),
        conversation_strategies=dict(document["conversation_strategies"]),
        expected_end_day=int(document["expected_end_day"]),
        actual_main_ending_id=(
            str(document["actual_main_ending_id"])
            if document.get("actual_main_ending_id") is not None
            else None
        ),
        actual_sub_ending_id=(
            str(document["actual_sub_ending_id"])
            if document.get("actual_sub_ending_id") is not None
            else None
        ),
        semantic_state_hash=(
            str(document["semantic_state_hash"])
            if document.get("semantic_state_hash") is not None
            else None
        ),
    )


def load_witnesses(path: Path) -> tuple[EndingWitness, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if int(raw.get("schema_version", 0)) != 1:
        raise ValueError("unsupported acceptance route profile schema_version")
    profiles = raw.get("profiles")
    if not isinstance(profiles, list):
        raise ValueError("acceptance route profiles must be a list")
    templates = raw.get("decision_policy_templates", {})
    if not isinstance(templates, dict) or not all(
        isinstance(value, dict) for value in templates.values()
    ):
        raise ValueError("decision_policy_templates must be an object of objects")
    main_overrides = raw.get("main_ending_policy_overrides", {})
    sub_overrides = raw.get("sub_ending_policy_overrides", {})
    for field_name, values in (
        ("main_ending_policy_overrides", main_overrides),
        ("sub_ending_policy_overrides", sub_overrides),
    ):
        if not isinstance(values, dict) or not all(
            isinstance(value, dict) for value in values.values()
        ):
            raise ValueError(f"{field_name} must be an object of objects")
    forbidden = _walk_forbidden(raw)
    if forbidden:
        raise ValueError("forbidden route field: " + ", ".join(forbidden))
    return tuple(
        _as_witness(
            item,
            index=index,
            decision_policy_templates=templates,
            main_ending_policy_overrides=main_overrides,
            sub_ending_policy_overrides=sub_overrides,
        )
        for index, item in enumerate(profiles)
    )


def load_contract_terms(path: Path) -> dict[str, dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    values = raw.get("contract_terms", {})
    if not isinstance(values, dict) or not all(
        isinstance(item, dict) for item in values.values()
    ):
        raise ValueError("contract_terms must be an object of objects")
    forbidden = _walk_forbidden(values, "$.contract_terms")
    if forbidden:
        raise ValueError("forbidden contract route field: " + ", ".join(forbidden))
    return {str(key): dict(value) for key, value in values.items()}


def validate_witnesses(
    witnesses: Iterable[EndingWitness],
    package: ScriptPackage,
) -> WitnessCoverage:
    items = tuple(witnesses)
    published_mains = {item.ending_id for item in package.main_endings}
    published_subs = {item.sub_ending_id for item in package.sub_endings}
    sub_parent = {
        item.sub_ending_id: item.main_ending_id for item in package.sub_endings
    }
    invalid_targets: list[str] = []
    invalid_patches: list[str] = []
    seen: set[str] = set()
    duplicates: set[str] = set()
    covered_mains: set[str] = set()
    covered_subs: set[str] = set()

    for item in items:
        if item.route_id in seen:
            duplicates.add(item.route_id)
        seen.add(item.route_id)
        forbidden = _walk_forbidden(item.to_dict(), f"route:{item.route_id}")
        invalid_patches.extend(forbidden)
        for main_id in item.target_main_ending_ids:
            if main_id not in published_mains:
                invalid_targets.append(f"{item.route_id}:unknown_main:{main_id}")
            else:
                covered_mains.add(main_id)
        for sub_id in item.target_sub_ending_ids:
            if sub_id not in published_subs:
                invalid_targets.append(f"{item.route_id}:unknown_sub:{sub_id}")
                continue
            covered_subs.add(sub_id)
            parent = sub_parent[sub_id]
            if parent not in item.target_main_ending_ids:
                invalid_targets.append(
                    f"{item.route_id}:sub_parent_mismatch:{sub_id}:{parent}"
                )
        if item.expected_end_day != 90:
            invalid_targets.append(
                f"{item.route_id}:unexpected_end_day:{item.expected_end_day}"
            )
        for decision_id, configured_choice in item.decision_policy.items():
            decision = package.decisions.get(decision_id)
            if decision is None:
                invalid_targets.append(
                    f"{item.route_id}:unknown_decision:{decision_id}"
                )
                continue
            option_id = (
                str(configured_choice.get("option_id", ""))
                if isinstance(configured_choice, dict)
                else str(configured_choice)
            )
            if option_id not in {option.option_id for option in decision.options}:
                invalid_targets.append(
                    f"{item.route_id}:unknown_option:{decision_id}:{option_id}"
                )
        if item.actual_main_ending_id is not None and (
            item.actual_main_ending_id not in item.target_main_ending_ids
        ):
            invalid_targets.append(
                f"{item.route_id}:actual_main_mismatch:{item.actual_main_ending_id}"
            )
        if item.actual_sub_ending_id is not None and (
            item.actual_sub_ending_id not in item.target_sub_ending_ids
        ):
            invalid_targets.append(
                f"{item.route_id}:actual_sub_mismatch:{item.actual_sub_ending_id}"
            )

    return WitnessCoverage(
        main_ending_ids=frozenset(covered_mains),
        sub_ending_ids=frozenset(covered_subs),
        invalid_state_patches=tuple(sorted(set(invalid_patches))),
        invalid_targets=tuple(sorted(set(invalid_targets))),
        duplicate_route_ids=tuple(sorted(duplicates)),
    )


def discover_witnesses(
    package: ScriptPackage,
    route_driver: RouteDriver,
) -> tuple[EndingWitness, ...]:
    """Replay profiles through a caller-provided formal-API route driver."""

    discovered: list[EndingWitness] = []
    for profile in route_driver.profiles:
        result = route_driver.run(profile)
        story_day = int(result.get("story_day", 0))
        if story_day != profile.expected_end_day:
            raise AssertionError(
                f"{profile.route_id} ended on D{story_day}, expected "
                f"D{profile.expected_end_day}"
            )
        actual_main = str(result.get("main_ending_id", ""))
        actual_sub = str(result.get("sub_ending_id", ""))
        if actual_main not in profile.target_main_ending_ids:
            raise AssertionError(
                f"{profile.route_id} reached {actual_main}, expected "
                f"{profile.target_main_ending_ids}"
            )
        if actual_sub not in profile.target_sub_ending_ids:
            raise AssertionError(
                f"{profile.route_id} reached {actual_sub}, expected "
                f"{profile.target_sub_ending_ids}"
            )
        discovered.append(EndingWitness(
            route_id=profile.route_id,
            target_main_ending_ids=profile.target_main_ending_ids,
            target_sub_ending_ids=profile.target_sub_ending_ids,
            origin_id=profile.origin_id,
            decision_policy=profile.decision_policy,
            daily_action_policy=profile.daily_action_policy,
            conversation_strategies=profile.conversation_strategies,
            expected_end_day=profile.expected_end_day,
            actual_main_ending_id=actual_main,
            actual_sub_ending_id=actual_sub,
            semantic_state_hash=semantic_state_hash(dict(result.get("state", {}))),
        ))
    coverage = validate_witnesses(discovered, package)
    if not coverage.is_complete:
        raise AssertionError(f"invalid ending witness coverage: {coverage}")
    return tuple(discovered)
