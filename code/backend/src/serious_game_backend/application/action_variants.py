from __future__ import annotations

from serious_game_backend.domain.enums import AvailabilityMode
from serious_game_backend.domain.game_session import GameSession
from serious_game_backend.domain.script_package import ScriptPackage
from serious_game_backend.application.npc_relationship_service import (
    NPCRelationshipService,
)


TARGET_SELECTION_RULES = {
    "household_visit": {"minimum": 1, "maximum": 1},
    "cadre_interview": {"minimum": 1, "maximum": 3},
    "leadership_meeting": {"minimum": 2, "maximum": 8},
    "inspect_archives": {"minimum": 1, "maximum": 100},
}


def participant_rules(action_id: str) -> dict[str, int]:
    return dict(TARGET_SELECTION_RULES[action_id])


def configured_variants(package: ScriptPackage) -> tuple[dict, ...]:
    return tuple(
        dict(item)
        for item in (package.governance_config or {}).get("action_variants", ())
    )


def variant_availability(
    session: GameSession,
    variant: dict,
) -> tuple[bool, str | None]:
    if not variant.get("enabled", False):
        return False, str(variant.get("unavailable_reason") or "当前版本尚未开放")
    story_day = session.game_state.story_day
    unlock_day = int(variant["unlock_day"])
    if story_day < unlock_day:
        return False, f"第 {unlock_day} 日后开放"
    required = set(variant.get("required_flags", ()))
    if not required.issubset(session.flags):
        return False, "必要剧情或材料条件尚未满足"
    required_any = set(variant.get("required_any_flags", ()))
    if required_any and not required_any.intersection(session.flags):
        return False, "必要剧情或材料条件尚未满足"
    forbidden = set(variant.get("forbidden_flags", ()))
    if forbidden.intersection(session.flags):
        return False, "当前状态不允许再次办理"
    return True, None


def variant_target_choices(
    session: GameSession,
    package: ScriptPackage,
    variant: dict,
) -> list[dict]:
    target_kind = str(variant["target_kind"])
    legal_ids = set(str(item) for item in variant.get("legal_target_ids", ()))
    if target_kind == "available_archive":
        return [
            {"target_id": item.archive_id, "label": item.title}
            for item in session.archive_records.values()
            if item.status == "available"
        ]
    if target_kind == "location":
        return [
            {"target_id": item.location_id, "label": item.name}
            for item in package.map_locations
            if item.location_id in legal_ids
            and session.game_state.story_day >= item.unlock_day
            and item.required_flags.issubset(session.flags)
        ]
    profiles = {item.npc_id: item.name for item in package.npc_profiles}
    visible_ids = visible_governance_npc_ids(session, package)
    return [
        {"target_id": npc_id, "label": profiles[npc_id]}
        for npc_id in variant.get("legal_target_ids", ())
        if npc_id in profiles
        and npc_id in session.npc_states
        and npc_id in visible_ids
    ]


def public_variant(
    session: GameSession,
    package: ScriptPackage,
    variant: dict,
) -> dict:
    tier = package.action_cost_tier(session.game_state.story_day).value
    available, reason = variant_availability(session, variant)
    location_names = {item.location_id: item.name for item in package.map_locations}
    return {
        "variant_id": variant["variant_id"],
        "action_id": variant["action_id"],
        "name": variant["name"],
        "description": variant.get("description", variant["visible_result"]),
        "cost_action_points": int(variant["action_point_costs"][tier]),
        "resource_cost_mode": variant["resource_cost_mode"],
        "resource_costs": list(variant["resource_costs"]),
        "visible_result": variant["visible_result"],
        "legal_location_ids": list(variant["legal_location_ids"]),
        "location_choices": [
            {
                "location_id": location_id,
                "label": str(
                    variant.get("location_labels", {}).get(
                        location_id, location_names.get(location_id, location_id)
                    )
                ),
            }
            for location_id in variant["legal_location_ids"]
        ],
        "target_kind": variant["target_kind"],
        "target_choices": variant_target_choices(session, package, variant),
        "participant_rules": participant_rules(str(variant["action_id"])),
        "available": available,
        "unavailable_reason": reason,
    }


def find_variant(package: ScriptPackage, variant_id: str) -> dict | None:
    return next(
        (
            item
            for item in configured_variants(package)
            if item.get("variant_id") == variant_id
        ),
        None,
    )


def visible_governance_npc_ids(
    session: GameSession,
    package: ScriptPackage,
) -> set[str]:
    if package.gameplay_schema_version >= 4:
        NPCRelationshipService.synchronize(session, package)
        return set(session.known_npc_ids)
    visible = set(
        (package.governance_config or {}).get("initial_visible_npc_ids", ())
    )
    story_day = session.game_state.story_day
    for opportunity in package.interaction_opportunities:
        if opportunity.availability_mode is AvailabilityMode.CLOSED:
            continue
        if story_day < opportunity.day_min:
            continue
        if not opportunity.requires_flags.issubset(session.flags):
            continue
        if not opportunity.requires_events.issubset(session.triggered_events):
            continue
        visible.add(opportunity.npc_id)
    return visible


def default_npc_location(npc_id: str) -> str:
    if npc_id in {"npc_shi_wenbin", "npc_ke_qinian"}:
        return "loc_environment_station"
    if npc_id in {"npc_he_tiezhu", "npc_yuan_guilan", "npc_luo_jian"}:
        return "loc_county_hospital"
    if npc_id == "npc_liu_san":
        return "loc_abandoned_grain_station"
    if npc_id in {
        "npc_zhou_dashan", "npc_zhou_kuiyuan", "npc_zhou_mancang",
        "npc_wu_xiuying", "npc_tan_laoliu", "npc_ma_changshun",
        "npc_ning_dehai", "npc_yang_bo", "npc_lao_juetou",
        "npc_miao_xiwang", "npc_deng_shouben", "npc_wang_fang",
    }:
        return "loc_liulin_village"
    return "loc_county_government"
