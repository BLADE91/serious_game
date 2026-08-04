from __future__ import annotations

import hashlib
import json
from pathlib import Path

from serious_game_backend.domain.action import ActionRule, ResourceActionDefinition
from serious_game_backend.domain.enums import ActionCostTier, AvailabilityMode, NPCStateTier
from serious_game_backend.domain.errors import ContentValidationError
from serious_game_backend.domain.interaction_opportunity import InteractionOpportunity
from serious_game_backend.domain.script_package import (
    BigFiveProfile,
    CalendarSegment,
    ContentCatalogEntry,
    EndingAppendixDefinition,
    FixedEventRule,
    MainEndingDefinition,
    MapLocationDefinition,
    HouseholdDefinition,
    LimitedHouseholdSignatory,
    MetricBand,
    NPCProfileStub,
    ScriptPackage,
    SubEndingDefinition,
)
from serious_game_backend.domain.story import (
    AvailabilityClause,
    ConditionalEffectDefinition,
    DecisionDefinition,
    DecisionOptionDefinition,
    FactDefinition,
    NarrativeBlock,
    OriginDefinition,
    ScriptedEffects,
    StoryDayDefinition,
)


REQUIRED_FILES = (
    "package_manifest.json",
    "numbers.json",
    "action_rules.json",
    "story_calendar.json",
    "event_rules.json",
    "npc_profiles.json",
    "flags.json",
    "interaction_opportunities.json",
    "story_beats.json",
    "decisions.json",
    "origins.json",
    "facts.json",
    "public_briefing.json",
    "map_locations.json",
    "ending_rules.json",
    "content_catalog.json",
)

EXPECTED_ORIGINS = {
    "technical",
    "grassroots",
    "integrity",
    "parachute",
    "young",
}

EXPECTED_ANCHORS = {
    "event_d31_municipal_inspection_arrival": 31,
    "event_d45_municipal_inspection_departure": 45,
    "event_d59_environmental_reception_arrival": 59,
    "event_d90_final_acceptance": 90,
}

EXPECTED_DECISION_CATALOG = {
    f"DP{chapter}-{index:02d}"
    for chapter, count in {1: 9, 2: 10, 3: 10, 4: 11, 5: 12, 6: 10}.items()
    for index in range(1, count + 1)
}
EXPECTED_EVENT_CATALOG = {
    "EV1-01", "EV1-02", "EV1-03", "EV2-01", "EV3-01",
    "EV4-01", "EV4-02", "EV4-03", "EV4-04", "EV5-01",
    "EV5-02", "EV5-03", "EV6-01", "EV6-02",
}
EXPECTED_SUPPORTING_RUNTIME = {
    "dp4_roster_disposition",
    "ev3_01_followup",
    "dp5_04_recovery",
    "dp5_05_recovery",
}
GAMEPLAY_V3_SUPPORTING_RUNTIME = {"dp3_tea_disposition"}
ON_DEMAND_SUPPORTING_RUNTIME = {"dp5_04_recovery", "dp5_05_recovery"}
ALLOWED_STATE_VALUES = {
    "lead_roster_disposition": {
        "未获取", "己方封存", "呈交上级", "交给记者", "被销毁"
    }
}
PLAYER_TEXT_INTERNAL_MARKERS = (
    "开启旗标",
    "关闭旗标",
    "显示位",
    "本节点",
    "结局轴",
    "状态量",
    "代码照此算",
    "行动点重置",
    "轴 T",
    "flag_",
)


class FileScriptPackageLoader:
    def load_all(self, root: Path) -> list[ScriptPackage]:
        if not root.exists():
            raise ContentValidationError(f"剧本包目录不存在：{root}")
        packages = [self.load(path) for path in sorted(root.iterdir()) if path.is_dir()]
        if not packages:
            raise ContentValidationError("没有可加载的剧本包")
        ids = [item.package_id for item in packages]
        if len(ids) != len(set(ids)):
            raise ContentValidationError("package_id 重复")
        return packages

    def load(self, package_dir: Path) -> ScriptPackage:
        missing = [name for name in REQUIRED_FILES if not (package_dir / name).is_file()]
        if missing:
            raise ContentValidationError("剧本包缺少文件", details={"missing": missing})
        manifest = self._json(package_dir / "package_manifest.json")
        computed_hash = self.compute_content_hash(package_dir)
        declared_hash = str(manifest.get("content_hash", ""))
        if manifest.get("status") == "published" and declared_hash != computed_hash:
            raise ContentValidationError(
                "published 剧本包内容哈希不匹配",
                details={"declared": declared_hash, "computed": computed_hash},
            )

        numbers = self._json(package_dir / "numbers.json")
        actions = self._load_actions(self._json(package_dir / "action_rules.json"))
        resource_actions = self._load_resource_actions(
            self._json(package_dir / "resource_actions.json")
            if (package_dir / "resource_actions.json").is_file()
            else {"actions": []}
        )
        households = self._load_households(
            self._json(package_dir / "households.json")
            if (package_dir / "households.json").is_file()
            else {"households": []}
        )
        limited_household_signatories = self._load_limited_household_signatories(
            self._json(package_dir / "household_signatories.json")
            if (package_dir / "household_signatories.json").is_file()
            else {"signatories": []}
        )
        governance_config = (
            self._json(package_dir / "governance_config.json")
            if (package_dir / "governance_config.json").is_file()
            else {}
        )
        calendar = self._load_calendar(self._json(package_dir / "story_calendar.json"))
        events = self._load_events(self._json(package_dir / "event_rules.json"))
        profiles = self._load_profiles(self._json(package_dir / "npc_profiles.json"))
        opportunities = self._load_opportunities(
            self._json(package_dir / "interaction_opportunities.json")
        )
        story_days = self._load_story_days(self._json(package_dir / "story_beats.json"))
        decisions = self._load_decisions(self._json(package_dir / "decisions.json"))
        origins = self._load_origins(self._json(package_dir / "origins.json"))
        facts = self._load_facts(self._json(package_dir / "facts.json"))
        public_briefing = self._load_public_briefing(
            self._json(package_dir / "public_briefing.json"), actions
        )
        map_locations = self._load_map_locations(
            self._json(package_dir / "map_locations.json")
        )
        main_endings, sub_endings, appendices = self._load_endings(
            self._json(package_dir / "ending_rules.json")
        )
        catalog_doc = self._json(package_dir / "content_catalog.json")
        decision_catalog = self._load_catalog(catalog_doc, "decisions")
        event_catalog = self._load_catalog(catalog_doc, "events")
        flags_doc = self._json(package_dir / "flags.json")
        registered_flags = frozenset(str(item) for item in flags_doc.get("registered_flags", []))
        social_rules = (
            self._json(package_dir / "social_rules.json")
            if (package_dir / "social_rules.json").is_file()
            else {}
        )
        gameplay_schema_version = int(manifest.get("gameplay_schema_version", 1))
        metric_bands = self._load_metric_bands(numbers)
        role_prompt_path = package_dir / "prompt_templates" / "role_turn_system.md"
        if not role_prompt_path.is_file():
            raise ContentValidationError("剧本包缺少角色回合系统提示词")
        role_turn_prompt = role_prompt_path.read_text(encoding="utf-8").strip()
        if not role_turn_prompt:
            raise ContentValidationError("角色回合系统提示词不能为空")
        self._validate(
            actions,
            calendar,
            events,
            profiles,
            opportunities,
            metric_bands,
            story_days,
            decisions,
            registered_flags,
            origins,
            facts,
            map_locations,
            main_endings,
            sub_endings,
            appendices,
            decision_catalog,
            event_catalog,
            resource_actions,
            households,
            limited_household_signatories,
            governance_config,
            gameplay_schema_version=gameplay_schema_version,
            status=str(manifest["status"]),
        )
        self._validate_night_agents(
            social_rules,
            profiles=profiles,
            registered_flags=registered_flags,
        )
        return ScriptPackage(
            package_id=str(manifest["package_id"]),
            package_version=str(manifest["package_version"]),
            content_hash=computed_hash,
            status=str(manifest["status"]),
            title=str(manifest["title"]),
            action_rules=actions,
            calendar_segments=calendar,
            fixed_events=events,
            metric_bands=metric_bands,
            npc_profiles=profiles,
            story_days=story_days,
            decisions=decisions,
            origins=origins,
            facts=facts,
            public_briefing=public_briefing,
            resource_actions=resource_actions,
            households=households,
            initial_state=dict(numbers.get("initial_state", {})),
            limited_household_signatories=limited_household_signatories,
            governance_config=governance_config,
            gameplay_schema_version=gameplay_schema_version,
            origin_npc_attitude_modifiers={
                str(origin_id): {
                    str(npc_id): int(delta) for npc_id, delta in values.items()
                }
                for origin_id, values in social_rules.get(
                    "origin_npc_attitude_modifiers", {}
                ).items()
            },
            trust_rules=dict(social_rules.get("trust_rules", {})),
            npc_relationships=tuple(
                dict(item) for item in social_rules.get("npc_relationships", [])
            ),
            night_agent_scenes=tuple(
                dict(item) for item in social_rules.get("night_agent_scenes", [])
            ),
            night_agent_actions={
                str(item["action_id"]): dict(item)
                for item in social_rules.get("night_agent_actions", [])
            },
            npc_social_roles={
                str(npc_id): tuple(str(role) for role in roles)
                for npc_id, roles in social_rules.get(
                    "npc_social_roles", {}
                ).items()
            },
            interaction_opportunities=opportunities,
            registered_flags=registered_flags,
            map_locations=map_locations,
            main_endings=main_endings,
            sub_endings=sub_endings,
            ending_appendices=appendices,
            decision_catalog=decision_catalog,
            event_catalog=event_catalog,
            source_sha256=str(catalog_doc.get("source_sha256", "")),
            role_turn_prompt=role_turn_prompt,
            role_turn_prompt_version="role-turn-v2",
        )

    @staticmethod
    def _validate_night_agents(
        document: dict,
        *,
        profiles: tuple[NPCProfileStub, ...],
        registered_flags: frozenset[str],
    ) -> None:
        npc_ids = {item.npc_id for item in profiles}
        social_roles = document.get("npc_social_roles", {})
        if not set(social_roles).issubset(npc_ids):
            raise ContentValidationError("NPC社会标签引用未知人物")
        if any(
            role not in {"crowd", "cadre"}
            for roles in social_roles.values()
            for role in roles
        ):
            raise ContentValidationError("NPC社会标签只能是 crowd 或 cadre")
        actions = document.get("night_agent_actions", [])
        action_ids = [str(item.get("action_id", "")) for item in actions]
        if any(not item for item in action_ids) or len(action_ids) != len(set(action_ids)):
            raise ContentValidationError("夜间 Agent 动作 ID 为空或重复")
        action_id_set = set(action_ids)
        scene_ids: set[str] = set()
        for scene in document.get("night_agent_scenes", []):
            scene_id = str(scene.get("scene_id", ""))
            participants = set(
                scene.get("candidate_ids", scene.get("participant_ids", ()))
            )
            if not scene_id or scene_id in scene_ids:
                raise ContentValidationError("夜间 Agent 场景 ID 为空或重复")
            scene_ids.add(scene_id)
            if len(participants) < 2 or not participants.issubset(npc_ids):
                raise ContentValidationError(
                    "夜间 Agent 场景参与者无效",
                    details={"scene_id": scene_id},
                )
            if (
                scene.get("selection_mode", "fixed")
                not in {"fixed", "autonomous"}
                or int(scene.get("max_contacts_per_npc", 2)) < 0
            ):
                raise ContentValidationError(
                    "夜间 Agent 联系人选择规则无效",
                    details={"scene_id": scene_id},
                )
            unknown_actions = set(scene.get("action_ids", ())) - action_id_set
            if unknown_actions:
                raise ContentValidationError(
                    "夜间 Agent 场景引用未登记动作",
                    details={"scene_id": scene_id, "action_ids": sorted(unknown_actions)},
                )
        for action in actions:
            action_id = str(action["action_id"])
            if action.get("resolution", "unilateral") not in {
                "unilateral", "consensus"
            }:
                raise ContentValidationError(
                    "夜间 Agent 动作结算规则无效",
                    details={"action_id": action_id},
                )
            referenced_npcs = (
                set(action.get("actor_ids", ()))
                | set(action.get("allowed_target_ids", ()))
            )
            if not referenced_npcs.issubset(npc_ids):
                raise ContentValidationError(
                    "夜间 Agent 动作引用未知 NPC",
                    details={"action_id": action_id},
                )
            effects = action.get("effects", {})
            if "budget_remaining" in effects.get("ledger_deltas", {}):
                raise ContentValidationError(
                    "NPC夜间行动不得修改预算",
                    details={"action_id": action_id},
                )
            referenced_flags = (
                set(action.get("required_flags", ()))
                | set(action.get("required_any_flags", ()))
                | set(action.get("forbidden_flags", ()))
                | set(effects.get("open_flags", ()))
                | set(effects.get("close_flags", ()))
            )
            unknown_flags = referenced_flags - registered_flags
            if unknown_flags:
                raise ContentValidationError(
                    "夜间 Agent 动作引用未注册旗标",
                    details={"action_id": action_id, "flags": sorted(unknown_flags)},
                )

    @staticmethod
    def compute_content_hash(package_dir: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(item for item in package_dir.rglob("*") if item.is_file()):
            relative = path.relative_to(package_dir).as_posix()
            digest.update(relative.encode("utf-8"))
            if relative == "package_manifest.json":
                manifest = json.loads(path.read_text(encoding="utf-8"))
                manifest.pop("content_hash", None)
                data = json.dumps(
                    manifest,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            else:
                data = path.read_bytes()
            digest.update(b"\0")
            digest.update(data)
            digest.update(b"\0")
        return f"sha256:{digest.hexdigest()}"

    @staticmethod
    def _json(path: Path) -> dict:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ContentValidationError(f"无法读取 {path.name}: {exc}") from exc
        if not isinstance(value, dict):
            raise ContentValidationError(f"{path.name} 顶层必须是对象")
        return value

    @staticmethod
    def _load_actions(document: dict) -> dict[str, ActionRule]:
        result: dict[str, ActionRule] = {}
        for item in document.get("actions", []):
            action_id = str(item["action_id"])
            if action_id in result:
                raise ContentValidationError(f"action_id 重复：{action_id}")
            result[action_id] = ActionRule(
                action_id=action_id,
                name=str(item["name"]),
                category=str(item["category"]),
                effect_type=str(item["effect_type"]),
                costs={ActionCostTier(key): int(value) for key, value in item["costs"].items()},
                daily_cap=int(item["daily_cap"]) if item.get("daily_cap") is not None else None,
                half_day=bool(item.get("half_day", False)),
                hard_force=bool(item.get("hard_force", False)),
                precondition_flags_any=tuple(item.get("precondition_flags_any", [])),
            )
        return result

    @classmethod
    def _load_resource_actions(
        cls, document: dict
    ) -> dict[str, ResourceActionDefinition]:
        result: dict[str, ResourceActionDefinition] = {}
        for item in document.get("actions", []):
            action_id = str(item["action_id"])
            if action_id in result:
                raise ContentValidationError(f"资源 action_id 重复：{action_id}")
            result[action_id] = ResourceActionDefinition(
                action_id=action_id,
                executor_kind=str(item["executor_kind"]),
                enabled=bool(item.get("enabled", True)),
                unavailable_reason=(
                    str(item["unavailable_reason"])
                    if item.get("unavailable_reason") else None
                ),
                target_schema=dict(item.get("target_schema", {})),
                parameter_schema=dict(item.get("parameter_schema", {})),
                budget_cost=int(item.get("budget_cost", 0)),
                resource_ids=tuple(str(value) for value in item.get("resource_ids", [])),
                location_ids=tuple(str(value) for value in item.get("location_ids", [])),
                required_flags=frozenset(item.get("required_flags", [])),
                required_any_flags=frozenset(item.get("required_any_flags", [])),
                forbidden_flags=frozenset(item.get("forbidden_flags", [])),
                effects=cls._load_effects(item.get("effects", {})),
                result_fact_ids=frozenset(item.get("result_fact_ids", [])),
                narrative=str(item.get("narrative", "")),
            )
        return result

    @staticmethod
    def _load_households(document: dict) -> tuple[HouseholdDefinition, ...]:
        result: list[HouseholdDefinition] = []
        seen: set[str] = set()
        defaults = dict(document.get("defaults", {}))
        for raw_item in document.get("households", []):
            item = {**defaults, **raw_item}
            household_id = str(item["household_id"])
            if household_id in seen:
                raise ContentValidationError(f"户号重复：{household_id}")
            seen.add(household_id)
            result.append(HouseholdDefinition(
                household_id=household_id,
                representative_group=str(item["representative_group"]),
                representative_npc=str(item["representative_npc"]),
                group_index=int(item["group_index"]),
                is_shadow_household=bool(item["is_shadow_household"]),
                registered_population=int(item["registered_population"]),
                actual_residents=int(item["actual_residents"]),
                resettlement_population=int(item["resettlement_population"]),
                residential_structure=str(item["residential_structure"]),
                legal_residential_area_m2=float(item["legal_residential_area_m2"]),
                homestead_recognized_m2=float(item["homestead_recognized_m2"]),
                homestead_over_m2=float(item.get("homestead_over_m2", 0)),
                contracted_land_mu=float(item.get("contracted_land_mu", 0)),
                other_land_mu=float(item.get("other_land_mu", 0)),
                other_land_note=(
                    str(item["other_land_note"])
                    if item.get("other_land_note") else None
                ),
                business_area_m2=float(item.get("business_area_m2", 0)),
                attachments_profile=str(item.get("attachments_profile", "待清点")),
                grave_or_shrine_profile=str(item.get("grave_or_shrine_profile", "none")),
                hardship_tags=tuple(str(value) for value in item.get("hardship_tags", [])),
                medical_tags=tuple(str(value) for value in item.get("medical_tags", [])),
                employment_startup_tags=tuple(
                    str(value) for value in item.get("employment_startup_tags", [])
                ),
                resettlement_preference=str(item["resettlement_preference"]),
                ownership_status=str(item["ownership_status"]),
                signing_lock_flag=(
                    str(item["signing_lock_flag"])
                    if item.get("signing_lock_flag") is not None
                    else None
                ),
            ))
        return tuple(result)

    @staticmethod
    def _load_limited_household_signatories(
        document: dict,
    ) -> tuple[LimitedHouseholdSignatory, ...]:
        result: list[LimitedHouseholdSignatory] = []
        seen_households: set[str] = set()
        seen_names: set[str] = set()
        for item in document.get("signatories", []):
            household_id = str(item["household_id"])
            name = str(item["name"]).strip()
            if household_id in seen_households:
                raise ContentValidationError(f"有限签约人户号重复：{household_id}")
            if name in seen_names:
                raise ContentValidationError(f"有限签约人姓名重复：{name}")
            seen_households.add(household_id)
            seen_names.add(name)
            result.append(LimitedHouseholdSignatory(
                household_id=household_id,
                name=name,
                initial_position=str(item["initial_position"]),
                core_concern=str(item["core_concern"]),
                acceptance_condition=str(item["acceptance_condition"]),
                refusal_trigger=str(item["refusal_trigger"]),
                counteroffer_focus=str(item["counteroffer_focus"]),
            ))
        return tuple(result)

    @staticmethod
    def _load_calendar(document: dict) -> tuple[CalendarSegment, ...]:
        return tuple(CalendarSegment(
            day_start=int(item["day_start"]),
            day_end=int(item["day_end"]),
            chapter=int(item["chapter"]),
            cost_tier=ActionCostTier(item["cost_tier"]),
        ) for item in document.get("segments", []))

    @staticmethod
    def _load_events(document: dict) -> tuple[FixedEventRule, ...]:
        return tuple(FixedEventRule(
            event_id=str(item["event_id"]),
            story_day=int(item["story_day"]),
            title=str(item["title"]),
            visible_summary=str(item["visible_summary"]),
            trigger_type=str(item.get("trigger_type", "fixed")),
            required_flags=frozenset(item.get("required_flags", [])),
            required_any_flags=frozenset(item.get("required_any_flags", [])),
            forbidden_flags=frozenset(item.get("forbidden_flags", [])),
            forbidden_event_ids=frozenset(item.get("forbidden_event_ids", [])),
        ) for item in document.get("fixed_events", []))

    @staticmethod
    def _load_map_locations(document: dict) -> tuple[MapLocationDefinition, ...]:
        return tuple(MapLocationDefinition(
            location_id=str(item["location_id"]),
            name=str(item["name"]),
            description=str(item["description"]),
            unlock_day=int(item.get("unlock_day", 1)),
            linked_opportunity_ids=tuple(item.get("linked_opportunity_ids", [])),
            linked_event_ids=tuple(item.get("linked_event_ids", [])),
            linked_action_ids=tuple(item.get("linked_action_ids", [])),
            required_flags=frozenset(item.get("required_flags", [])),
        ) for item in document.get("locations", []))

    @staticmethod
    def _load_endings(document: dict):
        main = tuple(MainEndingDefinition(
            ending_id=str(item["ending_id"]),
            order=int(item["order"]),
            name=str(item["name"]),
            tone=str(item["tone"]),
            condition=dict(item["condition"]),
            free_axis=str(item["free_axis"]),
            sub_ending_ids=tuple(item["sub_ending_ids"]),
            text=str(item.get("text", "")),
            source_line=int(item.get("source_line", 0)),
        ) for item in document.get("main_endings", []))
        sub = tuple(SubEndingDefinition(
            sub_ending_id=str(item["sub_ending_id"]),
            main_ending_id=str(item["main_ending_id"]),
            axis=str(item["axis"]),
            axis_value=str(item["axis_value"]),
            title=str(item["title"]),
            text=str(item.get("text", "")),
            source_line=int(item.get("source_line", 0)),
        ) for item in document.get("sub_endings", []))
        appendices = tuple(EndingAppendixDefinition(
            appendix_id=str(item["appendix_id"]),
            title=str(item["title"]),
            source=str(item["source"]),
        ) for item in document.get("appendices", []))
        return main, sub, appendices

    @staticmethod
    def _load_catalog(document: dict, key: str) -> tuple[ContentCatalogEntry, ...]:
        return tuple(ContentCatalogEntry(
            content_id=str(item["content_id"]),
            chapter=int(item["chapter"]),
            source_line=int(item["source_line"]),
        ) for item in document.get(key, []))

    @classmethod
    def _load_profiles(cls, document: dict) -> tuple[NPCProfileStub, ...]:
        return tuple(NPCProfileStub(
            npc_id=str(item["npc_id"]),
            name=str(item["name"]),
            state_tier=NPCStateTier(item["state_tier"]),
            profile_id=str(item.get("profile_id") or item["npc_id"]),
            initial_attitude=int(item.get("initial_attitude", 50)),
            initial_anxiety=int(item.get("initial_anxiety", 50)),
            role_setting=str(item.get("role_setting", "")),
            big_five=cls._load_big_five(item.get("big_five")),
            source_line=int(item.get("source_line", 0)),
        ) for item in document.get("npcs", []))

    @staticmethod
    def _load_big_five(value) -> BigFiveProfile | None:
        if value is None:
            return None
        required = {
            "openness",
            "conscientiousness",
            "extraversion",
            "agreeableness",
            "neuroticism",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise ContentValidationError(
                "NPC 大五人格必须完整包含五个结构化字段",
                details={
                    "required": sorted(required),
                    "actual": sorted(value) if isinstance(value, dict) else [],
                },
            )
        if any(type(value[field_name]) is not int for field_name in required):
            raise ContentValidationError("NPC 大五人格分数必须是整数")
        try:
            return BigFiveProfile(**value)
        except (TypeError, ValueError) as exc:
            raise ContentValidationError("NPC 大五人格分数必须在 0 到 100 之间") from exc

    @classmethod
    def _load_opportunities(
        cls, document: dict
    ) -> tuple[InteractionOpportunity, ...]:
        contexts = document.get("conversation_contexts", {})
        return tuple(InteractionOpportunity(
            opportunity_id=str(item["opportunity_id"]),
            npc_id=str(item["npc_id"]),
            entry_type=str(item["entry_type"]),
            day_min=int(item["day_min"]),
            day_max=int(item["day_max"]),
            action_id=str(item["action_id"]),
            availability_mode=AvailabilityMode(item.get("availability_mode", "free")),
            requires_flags=frozenset(item.get("requires_flags", [])),
            requires_events=frozenset(item.get("requires_events", [])),
            closes_on_flags=frozenset(item.get("closes_on_flags", [])),
            allowed_fact_ids=tuple(item.get("allowed_fact_ids", [])),
            completion_flags=frozenset(item.get("completion_flags", [])),
            completion_fact_ids=frozenset(item.get("completion_fact_ids", [])),
            completion_blocks=cls._load_blocks(item.get("completion_blocks", [])),
            completion_effects=cls._load_effects(item.get("completion_effects", {})),
            completion_decision_id=item.get("completion_decision_id"),
            opening_narrative=str(
                contexts.get(item["opportunity_id"], {}).get("opening_narrative", "")
            ),
            conversation_goal=str(
                contexts.get(item["opportunity_id"], {}).get("conversation_goal", "")
            ),
            minimum_turns=int(item.get("completion_policy", {}).get("minimum_turns", 1)),
            completion_mode=str(
                item.get("completion_policy", {}).get("completion_mode", "minimum_turns")
            ),
            required_disclosure_ids=frozenset(
                item.get("completion_policy", {}).get("required_disclosure_ids", [])
            ),
            complete_on_player_exit=bool(
                item.get("completion_policy", {}).get("complete_on_player_exit", True)
            ),
            complete_on_npc_exit=bool(
                item.get("completion_policy", {}).get("complete_on_npc_exit", True)
            ),
        ) for item in document.get("opportunities", []))

    @staticmethod
    def _load_blocks(values: list[dict]) -> tuple[NarrativeBlock, ...]:
        return tuple(NarrativeBlock(
            block_id=str(item["block_id"]),
            kind=str(item.get("kind", "narration")),
            text=str(item["text"]),
            speaker=str(item["speaker"]) if item.get("speaker") else None,
            origin_ids=frozenset(item.get("origin_ids", [])),
            required_flags=frozenset(item.get("required_flags", [])),
            required_any_flags=frozenset(item.get("required_any_flags", [])),
            forbidden_flags=frozenset(item.get("forbidden_flags", [])),
        ) for item in values)

    @staticmethod
    def _load_origins(document: dict) -> dict[str, OriginDefinition]:
        result: dict[str, OriginDefinition] = {}
        for item in document.get("origins", []):
            origin_id = str(item["origin_id"])
            if origin_id in result:
                raise ContentValidationError(f"origin_id 重复：{origin_id}")
            result[origin_id] = OriginDefinition(
                origin_id=origin_id,
                title=str(item["title"]),
                description=str(item["description"]),
            )
        return result

    @staticmethod
    def _load_facts(document: dict) -> dict[str, FactDefinition]:
        result: dict[str, FactDefinition] = {}
        for item in document.get("facts", []):
            fact_id = str(item["fact_id"])
            if fact_id in result:
                raise ContentValidationError(f"fact_id 重复：{fact_id}")
            result[fact_id] = FactDefinition(
                fact_id=fact_id,
                title=str(item["title"]),
                text=str(item["text"]),
                category=str(item.get("category", "fact")),
                source_line=int(item.get("source_line", 0)),
                source_label=str(item.get("source_label", "剧情中已确认")),
                related_npc_ids=tuple(str(value) for value in item.get("related_npc_ids", [])),
                use_hint=str(item.get(
                    "use_hint",
                    "可在后续会谈、调查和决策中作为已掌握材料引用。",
                )),
                disclosure_tier=int(item.get(
                    "disclosure_tier",
                    2 if item.get("fact_id") in {
                        "fact_inspection_anchors", "fact_total_households"
                    } else 4 if item.get("category") == "evidence" else 3,
                )),
                owner_npc_ids=tuple(
                    str(value) for value in item.get(
                        "owner_npc_ids", item.get("related_npc_ids", [])
                    )
                ),
            )
        return result

    @staticmethod
    def _load_public_briefing(document: dict, actions: dict[str, ActionRule]) -> dict:
        required = {"mission", "dossiers", "compensation_policy", "authorities", "tool_guidance"}
        missing = sorted(required - document.keys())
        if missing:
            raise ContentValidationError(
                "public_briefing.json 缺少公开资料模块",
                details={"missing": missing},
            )
        dossiers = document.get("dossiers", [])
        if len(dossiers) != 5:
            raise ContentValidationError("县长案头必须正好包含五份背景卷宗")
        guidance = document.get("tool_guidance", {})
        if set(guidance) != set(actions):
            raise ContentValidationError(
                "公开工具说明必须覆盖全部行动规则",
                details={
                    "missing": sorted(set(actions) - set(guidance)),
                    "unknown": sorted(set(guidance) - set(actions)),
                },
            )
        return document

    @classmethod
    def _load_story_days(cls, document: dict) -> dict[int, StoryDayDefinition]:
        result: dict[int, StoryDayDefinition] = {}
        for item in document.get("beats", []):
            story_day = int(item["story_day"])
            if story_day in result:
                raise ContentValidationError(f"story beat 日期重复：D{story_day}")
            result[story_day] = StoryDayDefinition(
                beat_id=str(item["beat_id"]),
                story_day=story_day,
                chapter=int(item["chapter"]),
                day_mode=str(item["day_mode"]),
                title=str(item["title"]),
                allow_actions=bool(item.get("allow_actions", True)),
                allow_end_day=bool(item.get("allow_end_day", True)),
                end_day_requires_flags=frozenset(
                    item.get("end_day_requires_flags", [])
                ),
                opening_blocks=cls._load_blocks(item.get("opening_blocks", [])),
                opening_decision_id=(
                    str(item["opening_decision_id"])
                    if item.get("opening_decision_id")
                    else None
                ),
                decision_ids=tuple(item.get("decision_ids", [])),
                night_blocks=cls._load_blocks(item.get("night_blocks", [])),
                night_effects=cls._load_effects(item.get("night_effects", {})),
                night_conditional_effects=tuple(
                    ConditionalEffectDefinition(
                        effects=cls._load_effects(branch.get("effects", {})),
                        replace_base=bool(branch.get("replace_base", False)),
                        required_flags=frozenset(branch.get("required_flags", [])),
                        required_any_flags=frozenset(branch.get("required_any_flags", [])),
                        forbidden_flags=frozenset(branch.get("forbidden_flags", [])),
                        required_state_values={
                            str(key): str(value)
                            for key, value in branch.get("required_state_values", {}).items()
                        },
                        forbidden_state_values={
                            str(key): frozenset(str(value) for value in values)
                            for key, values in branch.get("forbidden_state_values", {}).items()
                        },
                        minimum_ledger_values={
                            str(key): int(value)
                            for key, value in branch.get("minimum_ledger_values", {}).items()
                        },
                    )
                    for branch in item.get("night_conditional_effects", [])
                ),
            )
        return result

    @staticmethod
    def _load_effects(value: dict) -> ScriptedEffects:
        deltas = {
            str(key): (
                (int(item[0]), int(item[1]))
                if isinstance(item, list)
                else (int(item), int(item))
            )
            for key, item in value.get("metric_deltas", {}).items()
        }
        ledger_deltas = {
            str(key): (
                (int(item[0]), int(item[1]))
                if isinstance(item, list)
                else (int(item), int(item))
            )
            for key, item in value.get("ledger_deltas", {}).items()
        }
        return ScriptedEffects(
            metric_deltas=deltas,
            ledger_deltas=ledger_deltas,
            open_flags=frozenset(value.get("open_flags", [])),
            close_flags=frozenset(value.get("close_flags", [])),
            state_assignments={
                str(key): str(item)
                for key, item in value.get("state_assignments", {}).items()
            },
        )

    @staticmethod
    def _load_decisions(document: dict) -> dict[str, DecisionDefinition]:
        result: dict[str, DecisionDefinition] = {}
        for item in document.get("decisions", []):
            decision_id = str(item["decision_id"])
            if decision_id in result:
                raise ContentValidationError(f"decision_id 重复：{decision_id}")
            options = []
            for option in item.get("options", []):
                deltas = {
                    str(key): (
                        (int(value[0]), int(value[1]))
                        if isinstance(value, list)
                        else (int(value), int(value))
                    )
                    for key, value in option.get("effects", {}).get(
                        "metric_deltas", {}
                    ).items()
                }
                ledger_deltas = {
                    str(key): (
                        (int(value[0]), int(value[1]))
                        if isinstance(value, list)
                        else (int(value), int(value))
                    )
                    for key, value in option.get("effects", {}).get(
                        "ledger_deltas", {}
                    ).items()
                }
                options.append(DecisionOptionDefinition(
                    option_id=str(option["option_id"]),
                    text=str(option["text"]),
                    consequence=str(option["consequence"]),
                    effects=ScriptedEffects(
                        metric_deltas=deltas,
                        ledger_deltas=ledger_deltas,
                        open_flags=frozenset(
                            option.get("effects", {}).get("open_flags", [])
                        ),
                        close_flags=frozenset(
                            option.get("effects", {}).get("close_flags", [])
                        ),
                        state_assignments={
                            str(key): str(value)
                            for key, value in option.get("effects", {}).get(
                                "state_assignments", {}
                            ).items()
                        },
                    ),
                    required_flags=frozenset(option.get("required_flags", [])),
                    required_any_flags=frozenset(option.get("required_any_flags", [])),
                    forbidden_flags=frozenset(option.get("forbidden_flags", [])),
                    required_state_values={
                        str(key): str(value)
                        for key, value in option.get("required_state_values", {}).items()
                    },
                    forbidden_state_values={
                        str(key): frozenset(str(item) for item in values)
                        for key, values in option.get("forbidden_state_values", {}).items()
                    },
                    minimum_ledger_values={
                        str(key): int(value)
                        for key, value in option.get("minimum_ledger_values", {}).items()
                    },
                    maximum_ledger_values={
                        str(key): int(value)
                        for key, value in option.get("maximum_ledger_values", {}).items()
                    },
                    availability_any=tuple(
                        AvailabilityClause(
                            required_flags=frozenset(clause.get("required_flags", [])),
                            required_any_flags=frozenset(clause.get("required_any_flags", [])),
                            forbidden_flags=frozenset(clause.get("forbidden_flags", [])),
                            required_state_values={
                                str(key): str(value)
                                for key, value in clause.get("required_state_values", {}).items()
                            },
                            forbidden_state_values={
                                str(key): frozenset(str(item) for item in values)
                                for key, values in clause.get("forbidden_state_values", {}).items()
                            },
                            minimum_ledger_values={
                                str(key): int(value)
                                for key, value in clause.get("minimum_ledger_values", {}).items()
                            },
                            maximum_ledger_values={
                                str(key): int(value)
                                for key, value in clause.get("maximum_ledger_values", {}).items()
                            },
                        )
                        for clause in option.get("availability_any", [])
                    ),
                    unavailable_reason=str(option.get("unavailable_reason", "条件不足")),
                    conditional_effects=tuple(
                        ConditionalEffectDefinition(
                            effects=FileScriptPackageLoader._load_effects(
                                branch.get("effects", {})
                            ),
                            replace_base=bool(branch.get("replace_base", False)),
                            required_flags=frozenset(branch.get("required_flags", [])),
                            required_any_flags=frozenset(branch.get("required_any_flags", [])),
                            forbidden_flags=frozenset(branch.get("forbidden_flags", [])),
                            required_state_values={
                                str(key): str(value)
                                for key, value in branch.get("required_state_values", {}).items()
                            },
                            forbidden_state_values={
                                str(key): frozenset(str(item) for item in values)
                                for key, values in branch.get("forbidden_state_values", {}).items()
                            },
                            minimum_ledger_values={
                                str(key): int(value)
                                for key, value in branch.get("minimum_ledger_values", {}).items()
                            },
                        )
                        for branch in option.get("conditional_effects", [])
                    ),
                ))
            result[decision_id] = DecisionDefinition(
                decision_id=decision_id,
                story_day=int(item["story_day"]),
                title=str(item["title"]),
                prompt=str(item["prompt"]),
                options=tuple(options),
                followup_blocks=FileScriptPackageLoader._load_blocks(
                    item.get("followup_blocks", [])
                ),
                action_point_cost=int(item.get("action_point_cost", 0)),
                skippable=bool(item.get("skippable", False)),
                input_kind=str(item.get("input_kind", "choice")),
                input_schema=dict(item.get("input_schema", {})),
                required_flags=frozenset(item.get("required_flags", [])),
                required_any_flags=frozenset(item.get("required_any_flags", [])),
                forbidden_flags=frozenset(item.get("forbidden_flags", [])),
                early_day=(int(item["early_day"]) if item.get("early_day") else None),
                early_required_flags=frozenset(item.get("early_required_flags", [])),
                presentation_blocks=FileScriptPackageLoader._load_blocks(
                    item.get("presentation_blocks", [])
                ),
            )
        return result

    @staticmethod
    def _load_metric_bands(document: dict) -> dict[str, tuple[MetricBand, ...]]:
        return {
            key: tuple(
                MetricBand(int(item["min"]), int(item["max"]), str(item["label"]))
                for item in items
            )
            for key, items in document.get("visible_metric_bands", {}).items()
        }

    @staticmethod
    def _validate(
        actions,
        calendar,
        events,
        profiles,
        opportunities,
        metric_bands,
        story_days,
        decisions,
        registered_flags,
        origins,
        facts,
        map_locations,
        main_endings,
        sub_endings,
        appendices,
        decision_catalog,
        event_catalog,
        resource_actions,
        households,
        limited_household_signatories,
        governance_config,
        *,
        gameplay_schema_version: int,
        status: str,
    ) -> None:
        if len(actions) != 31:
            raise ContentValidationError(f"行动规则必须正好 31 项，当前 {len(actions)}")
        if gameplay_schema_version >= 2:
            if set(resource_actions) != set(actions):
                raise ContentValidationError(
                    "玩法 Schema v2 必须为 31 项工具逐一登记执行定义",
                    details={
                        "missing": sorted(set(actions) - set(resource_actions)),
                        "unknown": sorted(set(resource_actions) - set(actions)),
                    },
                )
            allowed_executors = {
                "conversation", "deterministic_analysis", "resource_dispatch",
                "policy_adjustment", "group_scene", "legal_procedure",
            }
            invalid = sorted(
                item.action_id for item in resource_actions.values()
                if item.executor_kind not in allowed_executors
            )
            if invalid:
                raise ContentValidationError(
                    "资源动作引用未知处理器", details={"action_ids": invalid}
                )
            non_conversation_opportunities = sorted(
                item.opportunity_id for item in opportunities
                if resource_actions[item.action_id].executor_kind != "conversation"
            )
            if non_conversation_opportunities:
                raise ContentValidationError(
                    "NPC 互动机会只能引用会谈类动作",
                    details={"opportunity_ids": non_conversation_opportunities},
                )
            if len(households) != 36:
                raise ContentValidationError(
                    "玩法 Schema v2 必须登记 36 户逐户底表",
                    details={"actual": len(households)},
                )
            npc_ids = {item.npc_id for item in profiles}
            unknown_household_npcs = sorted({
                item.representative_npc for item in households
                if item.representative_npc not in npc_ids
            })
            if unknown_household_npcs:
                raise ContentValidationError(
                    "逐户底表引用未知代表人物",
                    details={"npc_ids": unknown_household_npcs},
                )
            unknown_signing_locks = sorted({
                item.signing_lock_flag
                for item in households
                if item.signing_lock_flag
                and item.signing_lock_flag not in registered_flags
            })
            if unknown_signing_locks:
                raise ContentValidationError(
                    "逐户签约门槛引用未登记旗标",
                    details={"flags": unknown_signing_locks},
                )
            deprecated_signing_flags = sorted(
                flag
                for flag in registered_flags
                if flag.endswith(("已入账", "已签"))
            )
            if deprecated_signing_flags:
                raise ContentValidationError(
                    "合同权威模式不得登记已入账或已签类剧情旗标",
                    details={"flags": deprecated_signing_flags},
                )
            household_ids = {item.household_id for item in households}
            shadow_household_ids = {
                item.household_id for item in households if item.is_shadow_household
            }
            signatory_household_ids = {
                item.household_id for item in limited_household_signatories
            }
            if signatory_household_ids != shadow_household_ids:
                raise ContentValidationError(
                    "有限签约人必须与23个关联户逐户对应",
                    details={
                        "missing": sorted(shadow_household_ids - signatory_household_ids),
                        "unexpected": sorted(signatory_household_ids - household_ids),
                        "primary_households": sorted(
                            signatory_household_ids - shadow_household_ids
                        ),
                    },
                )
            allowed_positions = {"观望", "有条件接受", "倾向拒绝"}
            invalid_positions = sorted(
                {
                    item.initial_position
                    for item in limited_household_signatories
                    if item.initial_position not in allowed_positions
                }
            )
            if invalid_positions:
                raise ContentValidationError(
                    "有限签约人初始立场不在允许范围",
                    details={"positions": invalid_positions},
                )
            npc_names = {item.name for item in profiles}
            conflicting_names = sorted(
                item.name
                for item in limited_household_signatories
                if item.name in npc_names
            )
            if conflicting_names:
                raise ContentValidationError(
                    "有限签约人与完整NPC重名",
                    details={"names": conflicting_names},
                )
            empty_stances = sorted(
                item.household_id
                for item in limited_household_signatories
                if not all((
                    item.name,
                    item.core_concern,
                    item.acceptance_condition,
                    item.refusal_trigger,
                    item.counteroffer_focus,
                ))
            )
            if empty_stances:
                raise ContentValidationError(
                    "有限签约人立场字段不得为空",
                    details={"household_ids": empty_stances},
                )
            group_members: dict[str, list[HouseholdDefinition]] = {}
            representative_groups: dict[str, set[str]] = {}
            for household in households:
                group_members.setdefault(household.representative_group, []).append(
                    household
                )
                representative_groups.setdefault(
                    household.representative_npc,
                    set(),
                ).add(household.representative_group)
            reused_representatives = {
                npc_id: sorted(groups)
                for npc_id, groups in representative_groups.items()
                if len(groups) != 1
            }
            if reused_representatives:
                raise ContentValidationError(
                    "同一代表人物不能对应多个合同户群",
                    details={"representatives": reused_representatives},
                )
            invalid_groups = {}
            for group_name, members in group_members.items():
                primary = [item for item in members if not item.is_shadow_household]
                expected_indexes = list(range(1, len(members) + 1))
                actual_indexes = sorted(item.group_index for item in members)
                representative_npcs = {item.representative_npc for item in members}
                if (
                    len(primary) != 1
                    or actual_indexes != expected_indexes
                    or len(representative_npcs) != 1
                ):
                    invalid_groups[group_name] = {
                        "primary_count": len(primary),
                        "indexes": actual_indexes,
                        "representative_npcs": sorted(representative_npcs),
                    }
            if invalid_groups:
                raise ContentValidationError(
                    "合同批次必须由1个代表户和n个关联户构成",
                    details={"groups": invalid_groups},
                )
            base_actions = governance_config.get("base_actions", [])
            base_action_ids = {
                str(item.get("action_id", "")) for item in base_actions
            }
            expected_base_actions = {
                "household_visit",
                "cadre_interview",
                "leadership_meeting",
                "inspect_archives",
            }
            if base_action_ids != expected_base_actions:
                raise ContentValidationError(
                    "治理配置必须且只能登记四项基础行动",
                    details={
                        "missing": sorted(expected_base_actions - base_action_ids),
                        "unexpected": sorted(base_action_ids - expected_base_actions),
                    },
                )
            permissions = set(governance_config.get("permissions", {}))
            if permissions != {"1.1", "1.2", "1.3", "1.4", "1.5"}:
                raise ContentValidationError("治理配置权限必须完整登记1.1至1.5")
            if governance_config.get("contract_signing_authoritative") is not True:
                raise ContentValidationError("逐户合同必须是唯一真实签约入口")
            batch_gates = {
                str(npc_id): str(flag_id)
                for npc_id, flag_id in governance_config.get(
                    "contract_batch_gate_flags", {}
                ).items()
            }
            unknown_gate_npcs = sorted(set(batch_gates) - npc_ids)
            unknown_gate_flags = sorted(
                set(batch_gates.values()) - registered_flags
            )
            if unknown_gate_npcs or unknown_gate_flags:
                raise ContentValidationError(
                    "合同批次解锁配置引用未知人物或旗标",
                    details={
                        "npc_ids": unknown_gate_npcs,
                        "flags": unknown_gate_flags,
                    },
                )
            resource_ids = [
                str(item.get("resource_id", ""))
                for item in governance_config.get("resource_pools", [])
            ]
            if not resource_ids or len(resource_ids) != len(set(resource_ids)):
                raise ContentValidationError("治理资源池不能为空且资源ID不得重复")
            if sum(
                int(item["capacity"])
                for item in governance_config["resource_pools"]
                if item.get("category") == "housing"
            ) != 36:
                raise ContentValidationError("安置房资源池必须闭合为36套")
            initial_documents = governance_config.get("initial_documents", [])
            if not any(
                item.get("document_type") == "compensation_policy"
                and item.get("status") == "published"
                for item in initial_documents
            ):
                raise ContentValidationError("开局必须存在已发布的补偿安置方案")
            expected_totals = {
                "registered_population": 122,
                "resettlement_population": 122,
                "legal_residential_area_m2": 4745.0,
                "homestead_recognized_m2": 5950.0,
                "homestead_over_m2": 160.0,
                "contracted_land_mu": 82.8,
                "business_area_m2": 45.0,
            }
            actual_totals = {
                key: round(sum(float(getattr(item, key)) for item in households), 3)
                for key in expected_totals
            }
            mismatched = {
                key: {"expected": expected, "actual": actual_totals[key]}
                for key, expected in expected_totals.items()
                if actual_totals[key] != expected
            }
            if mismatched:
                raise ContentValidationError(
                    "36 户底表合计未闭合", details=mismatched
                )
        covered = []
        for segment in calendar:
            covered.extend(range(segment.day_start, segment.day_end + 1))
        if sorted(covered) != list(range(1, 91)) or len(covered) != 90:
            raise ContentValidationError("story_calendar 必须无重叠覆盖 D1-D90")
        anchors = {
            item.event_id: item.story_day
            for item in events
            if item.event_id in EXPECTED_ANCHORS
        }
        if anchors != EXPECTED_ANCHORS:
            raise ContentValidationError("D31/D45/D59/D90 固定事件锚点错误", details={"actual": anchors})
        for event in events:
            if event.trigger_type not in {"fixed", "conditional"}:
                raise ContentValidationError(f"事件触发类型非法：{event.event_id}")
            unknown = (
                event.required_flags
                | event.required_any_flags
                | event.forbidden_flags
            ) - registered_flags
            if unknown:
                raise ContentValidationError(
                    f"事件引用未知旗标：{event.event_id}",
                    details={"flags": sorted(unknown)},
                )
            unknown_events = event.forbidden_event_ids - {item.event_id for item in events}
            if unknown_events:
                raise ContentValidationError(
                    f"事件引用未知互斥事件：{event.event_id}",
                    details={"event_ids": sorted(unknown_events)},
                )
        if len(profiles) != 29 or len({item.npc_id for item in profiles}) != 29:
            raise ContentValidationError("非玩家人物实体必须是 29 个唯一 npc_id")
        deep_profiles = [item for item in profiles if item.state_tier.value == "deep"]
        limited_profiles = [item for item in profiles if item.state_tier.value == "limited"]
        ambient_profiles = [item for item in profiles if item.state_tier.value == "ambient"]
        if (len(deep_profiles), len(limited_profiles), len(ambient_profiles)) != (19, 9, 1):
            raise ContentValidationError(
                "最终剧本 7.4.1 固定要求 19/9/1 三档人物",
                details={
                    "deep": len(deep_profiles),
                    "limited": len(limited_profiles),
                    "ambient": len(ambient_profiles),
                },
            )
        incomplete_roles = [
            item.npc_id
            for item in profiles
            if item.npc_id != "npc_jiang_chongyue"
            if not item.role_setting.strip() or item.source_line <= 0
        ]
        # 五名轻量配角没有九维档案，母稿只要求身份与诉求。
        lightweight_ids = {
            "npc_deng_shouben", "npc_miao_xiwang", "npc_lao_juetou",
            "npc_luo_jian", "npc_cui_guanglin",
        }
        incomplete_roles = [item for item in incomplete_roles if item not in lightweight_ids]
        if incomplete_roles:
            raise ContentValidationError(
                "23 份九维人物档案必须携带母稿角色设定和来源行",
                details={"npc_ids": incomplete_roles},
            )
        npc_ids = {item.npc_id for item in profiles}
        opportunity_ids: set[str] = set()
        for item in opportunities:
            if item.opportunity_id in opportunity_ids:
                raise ContentValidationError(f"opportunity_id 重复：{item.opportunity_id}")
            opportunity_ids.add(item.opportunity_id)
            if item.npc_id not in npc_ids:
                raise ContentValidationError(f"互动机会引用未知 NPC：{item.npc_id}")
            if item.action_id not in actions:
                raise ContentValidationError(f"互动机会引用未知行动：{item.action_id}")
            if not item.opening_narrative.strip() or not item.conversation_goal.strip():
                raise ContentValidationError(
                    f"互动机会缺少玩家可见的前情提要或会谈方向：{item.opportunity_id}"
                )
        if status == "published":
            covered_npcs = {item.npc_id for item in opportunities}
            if covered_npcs != npc_ids:
                raise ContentValidationError(
                    "完整包必须为 29 名 NPC 各登记互动可用性",
                    details={"missing": sorted(npc_ids - covered_npcs)},
                )
            ambient_ids = {
                item.npc_id for item in profiles if item.state_tier.value == "ambient"
            }
            if any(
                item.npc_id in ambient_ids
                and item.availability_mode is not AvailabilityMode.CLOSED
                for item in opportunities
            ):
                raise ContentValidationError("环境人物不得开放自由文字互动")
        required_metrics = {
            "public_trust",
            "social_stability",
            "political_credit",
            "media_pressure",
            "cadre_discontent",
        }
        if set(metric_bands) != required_metrics:
            raise ContentValidationError("五项玩家可见体感指标定义不完整")
        for key, bands in metric_bands.items():
            values = []
            for band in bands:
                values.extend(range(band.minimum, band.maximum + 1))
            if values != list(range(0, 101)):
                raise ContentValidationError(f"{key} 档位必须无重叠覆盖 0-100")
        if status == "published" and sorted(story_days) != list(range(1, 91)):
            raise ContentValidationError("published story_beats 必须逐日覆盖 D1-D90")
        if status == "published":
            decision_catalog_ids = {item.content_id for item in decision_catalog}
            event_catalog_ids = {item.content_id for item in event_catalog}
            if decision_catalog_ids != EXPECTED_DECISION_CATALOG:
                raise ContentValidationError("完整包必须登记 62 个编号决策点")
            chapter_counts = {
                chapter: sum(item.chapter == chapter for item in decision_catalog)
                for chapter in range(1, 7)
            }
            if chapter_counts != {1: 9, 2: 10, 3: 10, 4: 11, 5: 12, 6: 10}:
                raise ContentValidationError(
                    "编号决策点章节计数错误", details={"actual": chapter_counts}
                )
            if event_catalog_ids != EXPECTED_EVENT_CATALOG:
                raise ContentValidationError("完整包必须登记 14 个突发事件")
            runtime_ids = {
                item.lower().replace("-", "_")
                for item in EXPECTED_DECISION_CATALOG | EXPECTED_EVENT_CATALOG
            }
            runtime_ids -= {"dp1_01", "ev1_01"}
            runtime_ids |= {
                "dp1_01_taskforce_faction_map", "ev1_01_reception_bag"
            }
            runtime_ids |= EXPECTED_SUPPORTING_RUNTIME
            if gameplay_schema_version >= 3:
                runtime_ids |= GAMEPLAY_V3_SUPPORTING_RUNTIME
            if set(decisions) != runtime_ids:
                raise ContentValidationError(
                    "完整包的 62 DP、14 EV 与强制配套处置点必须全部可提交"
                )
            scheduled = []
            for beat in story_days.values():
                if beat.opening_decision_id:
                    scheduled.append(beat.opening_decision_id)
                scheduled.extend(beat.decision_ids)
            scheduled_runtime_ids = runtime_ids - {
                item.lower().replace("-", "_")
                for item in EXPECTED_EVENT_CATALOG - {"EV1-01"}
            }
            scheduled_runtime_ids -= ON_DEMAND_SUPPORTING_RUNTIME
            if (
                len(scheduled) != len(set(scheduled))
                or set(scheduled) != scheduled_runtime_ids
            ):
                raise ContentValidationError("完整包决策必须且只能进入一次故事时钟")
            if not all(
                item.source_line > 0 for item in (*decision_catalog, *event_catalog)
            ):
                raise ContentValidationError("内容目录 source_line 非法")
            if not all(
                any(event.event_id == item for event in events)
                for item in EXPECTED_EVENT_CATALOG
            ):
                raise ContentValidationError("突发事件目录未全部进入事件状态机")
            if len(main_endings) != 24 or len(sub_endings) != 95:
                raise ContentValidationError("完整包必须包含 24 主结局和 95 亚结局")
            if not all(
                item.text.strip() and item.source_line > 0
                for item in (*main_endings, *sub_endings)
            ):
                raise ContentValidationError("24/95 结局必须携带母稿正文与来源行")
            if len(appendices) != 3:
                raise ContentValidationError("完整包必须包含 3 个结局附加位")
            if [item.order for item in main_endings] != list(range(1, 25)):
                raise ContentValidationError("主结局顺序必须为 1-24")
            if main_endings[-1].condition != {"always": True}:
                raise ContentValidationError("第 24 主结局必须是恒真兜底")
        allowed_day_modes = {"playable", "simulated", "transition", "ending"}
        allowed_effect_fields = {
            "public_trust",
            "social_stability",
            "political_credit",
            "media_pressure",
            "env_clue",
            "integrity",
            "cadre_discontent",
            "corruption_evidence",
        }
        allowed_ledger_fields = {
            "budget_remaining",
            "signed_households",
            "reported_signed_households",
        }
        allowed_ledger_condition_fields = allowed_ledger_fields | {
            "chapter_overtime_count",
        }
        if "" in registered_flags:
            raise ContentValidationError("registered_flags 不能包含空字符串")
        expected_origins = (
            EXPECTED_ORIGINS | {"mayor"}
            if gameplay_schema_version >= 2
            else EXPECTED_ORIGINS
        )
        if set(origins) != expected_origins:
            raise ContentValidationError(
                "开局身份定义不完整",
                details={"actual": sorted(origins)},
            )
        for origin in origins.values():
            if not origin.title.strip() or not origin.description.strip():
                raise ContentValidationError(f"出身字段不能为空：{origin.origin_id}")
        for fact in facts.values():
            if not fact.fact_id or not fact.title.strip() or not fact.text.strip():
                raise ContentValidationError(f"事实字段不能为空：{fact.fact_id}")
            if fact.disclosure_tier not in {1, 2, 3, 4}:
                raise ContentValidationError(f"事实吐露档位非法：{fact.fact_id}")
        if status == "published":
            if len(facts) < 16 or not all(item.source_line > 0 for item in facts.values()):
                raise ContentValidationError(
                    "完整包至少需要 16 条带来源行的结构化事实/线索"
                )
        beat_ids: set[str] = set()
        block_ids: set[str] = set()
        for day, beat in story_days.items():
            if (
                not beat.beat_id
                or not beat.title.strip()
                or not 1 <= day <= 90
                or beat.day_mode not in allowed_day_modes
            ):
                raise ContentValidationError(f"非法 story beat：{beat.beat_id}")
            if gameplay_schema_version >= 3 and any(
                marker in beat.title for marker in PLAYER_TEXT_INTERNAL_MARKERS
            ):
                raise ContentValidationError(
                    f"story beat 标题混入内部说明：{beat.beat_id}"
                )
            if beat.beat_id in beat_ids:
                raise ContentValidationError(f"beat_id 重复：{beat.beat_id}")
            beat_ids.add(beat.beat_id)
            matching = [item for item in calendar if item.contains(day)]
            if len(matching) != 1 or matching[0].chapter != beat.chapter:
                raise ContentValidationError(f"story beat 章节与日历不一致：{beat.beat_id}")
            if beat.opening_decision_id:
                decision = decisions.get(beat.opening_decision_id)
                if decision is None or decision.story_day != day:
                    raise ContentValidationError(
                        f"story beat 引用未知或跨日决策：{beat.opening_decision_id}"
                    )
            for decision_id in beat.decision_ids:
                decision = decisions.get(decision_id)
                if decision is None or decision.story_day != day:
                    raise ContentValidationError(
                        f"story beat 引用未知或跨日决策：{decision_id}"
                    )
            unknown_end_flags = beat.end_day_requires_flags - registered_flags
            if unknown_end_flags:
                raise ContentValidationError(
                    f"story beat 日终条件引用未知旗标：{beat.beat_id}",
                    details={"flags": sorted(unknown_end_flags)},
                )
            unknown_night_flags = (
                beat.night_effects.open_flags | beat.night_effects.close_flags
            ) - registered_flags
            if (
                gameplay_schema_version >= 3
                and "budget_remaining" in beat.night_effects.ledger_deltas
            ):
                raise ContentValidationError(
                    f"夜间剧情不得修改预算：{beat.beat_id}"
                )
            for branch in beat.night_conditional_effects:
                unknown_night_flags |= (
                    branch.required_flags
                    | branch.required_any_flags
                    | branch.forbidden_flags
                    | branch.effects.open_flags
                    | branch.effects.close_flags
                ) - registered_flags
                if set(branch.minimum_ledger_values) - allowed_ledger_fields:
                    raise ContentValidationError(
                        f"夜间规则引用未知台账字段：{beat.beat_id}"
                    )
                if (
                    gameplay_schema_version >= 3
                    and "budget_remaining" in branch.effects.ledger_deltas
                ):
                    raise ContentValidationError(
                        f"夜间条件结算不得修改预算：{beat.beat_id}"
                    )
            if unknown_night_flags:
                raise ContentValidationError(
                    f"夜间规则引用未知旗标：{beat.beat_id}",
                    details={"flags": sorted(unknown_night_flags)},
                )
            for block in (*beat.opening_blocks, *beat.night_blocks):
                if not block.block_id or not block.kind or not block.text.strip():
                    raise ContentValidationError(
                        f"story block 字段不能为空：{beat.beat_id}"
                    )
                if gameplay_schema_version >= 3 and any(
                    marker in block.text for marker in PLAYER_TEXT_INTERNAL_MARKERS
                ):
                    raise ContentValidationError(
                        f"story block 混入内部说明：{block.block_id}"
                    )
                if block.block_id in block_ids:
                    raise ContentValidationError(
                        f"story block ID 重复：{block.block_id}"
                    )
                block_ids.add(block.block_id)
                unknown_origins = block.origin_ids - set(origins)
                if unknown_origins:
                    raise ContentValidationError(
                        f"story block 引用未知出身：{block.block_id}",
                        details={"origins": sorted(unknown_origins)},
                    )
                unknown_block_flags = (
                    block.required_flags
                    | block.required_any_flags
                    | block.forbidden_flags
                ) - registered_flags
                if unknown_block_flags:
                    raise ContentValidationError(
                        f"story block 引用未知旗标：{block.block_id}",
                        details={"flags": sorted(unknown_block_flags)},
                    )

        opportunity_ids = {item.opportunity_id for item in opportunities}
        location_ids = [item.location_id for item in map_locations]
        if not map_locations or len(location_ids) != len(set(location_ids)):
            raise ContentValidationError("地图地点不能为空且 location_id 必须唯一")
        for location in map_locations:
            unknown_opportunities = (
                set(location.linked_opportunity_ids) - opportunity_ids
            )
            unknown_flags = location.required_flags - registered_flags
            unknown_events = set(location.linked_event_ids) - {item.event_id for item in events}
            unknown_actions = set(location.linked_action_ids) - set(actions)
            if unknown_opportunities or unknown_flags or unknown_events or unknown_actions:
                raise ContentValidationError(
                    f"地图地点引用悬空：{location.location_id}",
                    details={
                        "opportunities": sorted(unknown_opportunities),
                        "flags": sorted(unknown_flags),
                        "events": sorted(unknown_events),
                        "actions": sorted(unknown_actions),
                    },
                )
        main_ids = {item.ending_id for item in main_endings}
        sub_ids = {item.sub_ending_id for item in sub_endings}
        if len(main_ids) != len(main_endings) or len(sub_ids) != len(sub_endings):
            raise ContentValidationError("结局 ID 必须唯一")
        for ending in main_endings:
            if set(ending.sub_ending_ids) - sub_ids:
                raise ContentValidationError(f"主结局引用未知亚结局：{ending.ending_id}")
        for ending in sub_endings:
            if ending.main_ending_id not in main_ids:
                raise ContentValidationError(
                    f"亚结局引用未知主结局：{ending.sub_ending_id}"
                )
        referenced_sub_ids = [
            sub_id for ending in main_endings for sub_id in ending.sub_ending_ids
        ]
        if len(referenced_sub_ids) != len(set(referenced_sub_ids)) or set(
            referenced_sub_ids
        ) != sub_ids:
            raise ContentValidationError("95 个亚结局必须各归属且仅归属一个主结局")
        for ending in sub_endings:
            parent = next(item for item in main_endings if item.ending_id == ending.main_ending_id)
            if ending.axis != parent.free_axis:
                raise ContentValidationError(
                    f"亚结局自由轴与主结局不一致：{ending.sub_ending_id}"
                )
        for decision in decisions.values():
            unknown_decision_flags = (
                decision.required_flags
                | decision.required_any_flags
                | decision.forbidden_flags
                | decision.early_required_flags
            ) - registered_flags
            if unknown_decision_flags:
                raise ContentValidationError(
                    f"决策前置引用未知旗标：{decision.decision_id}"
                )
            if (
                not decision.decision_id
                or not decision.title.strip()
                or not decision.prompt.strip()
                or not 1 <= decision.story_day <= 90
            ):
                raise ContentValidationError(
                    f"决策基础字段非法：{decision.decision_id}"
                )
            if gameplay_schema_version >= 3 and any(
                marker in value
                for value in (decision.title, decision.prompt)
                for marker in PLAYER_TEXT_INTERNAL_MARKERS
            ):
                raise ContentValidationError(
                    f"决策玩家文本混入内部说明：{decision.decision_id}"
                )
            if decision.action_point_cost != 0:
                raise ContentValidationError(f"强制决策必须为 0 行动点：{decision.decision_id}")
            if decision.input_kind not in {"choice", "sorting", "allocation"}:
                raise ContentValidationError(
                    f"决策输入类型非法：{decision.decision_id}"
                )
            if decision.input_kind == "allocation":
                schema = decision.input_schema
                if (
                    int(schema.get("total", 0)) <= 0
                    or len(schema.get("fields", [])) < 2
                    or len(set(schema.get("fields", []))) != len(schema.get("fields", []))
                ):
                    raise ContentValidationError(
                        f"分配题输入协议非法：{decision.decision_id}"
                    )
            option_ids = [item.option_id for item in decision.options]
            sorting_ids = all(
                "_" in option_id
                and len(parts := option_id.split("_")) == len(set(parts))
                and all(part in {"a", "b", "c", "d", "e"} for part in parts)
                for option_id in option_ids
            )
            valid_count = (
                2 <= len(option_ids) <= 5
                or (sorting_ids and len(option_ids) in {6, 24, 120})
                or (decision.input_kind == "allocation" and option_ids == ["submit"])
            )
            if not valid_count or len(option_ids) != len(set(option_ids)):
                raise ContentValidationError(f"决策选项数量或 ID 非法：{decision.decision_id}")
            for option in decision.options:
                if (
                    not option.option_id
                    or not option.text.strip()
                    or not option.consequence.strip()
                ):
                    raise ContentValidationError(
                        f"决策选项字段不能为空：{decision.decision_id}"
                    )
                if gameplay_schema_version >= 3 and any(
                    marker in value
                    for value in (option.text, option.consequence)
                    for marker in PLAYER_TEXT_INTERNAL_MARKERS
                ):
                    raise ContentValidationError(
                        f"决策选项玩家文本混入内部说明："
                        f"{decision.decision_id}:{option.option_id}"
                    )
                unknown_fields = set(option.effects.metric_deltas) - allowed_effect_fields
                unknown_ledger_fields = (
                    set(option.effects.ledger_deltas) - allowed_ledger_fields
                )
                unknown_ledger_condition_fields = (
                    set(option.minimum_ledger_values)
                    | set(option.maximum_ledger_values)
                ) - allowed_ledger_condition_fields
                unknown_flags = (
                    option.effects.open_flags | option.effects.close_flags
                ) - registered_flags
                unknown_condition_flags = (
                    option.required_flags
                    | option.required_any_flags
                    | option.forbidden_flags
                ) - registered_flags
                for clause in option.availability_any:
                    unknown_condition_flags |= (
                        clause.required_flags
                        | clause.required_any_flags
                        | clause.forbidden_flags
                    ) - registered_flags
                    unknown_ledger_condition_fields |= (
                        set(clause.minimum_ledger_values)
                        | set(clause.maximum_ledger_values)
                    ) - allowed_ledger_condition_fields
                for branch in option.conditional_effects:
                    unknown_condition_flags |= (
                        branch.required_flags
                        | branch.required_any_flags
                        | branch.forbidden_flags
                        | branch.effects.open_flags
                        | branch.effects.close_flags
                    ) - registered_flags
                    if set(branch.minimum_ledger_values) - allowed_ledger_fields:
                        raise ContentValidationError(
                            f"条件结算引用未知台账字段：{decision.decision_id}"
                        )
                state_writes = dict(option.effects.state_assignments)
                state_conditions = {
                    **option.required_state_values,
                    **{
                        key: next(iter(values), "")
                        for key, values in option.forbidden_state_values.items()
                    },
                }
                for clause in option.availability_any:
                    state_conditions.update(clause.required_state_values)
                    state_conditions.update({
                        key: next(iter(values), "")
                        for key, values in clause.forbidden_state_values.items()
                    })
                for branch in option.conditional_effects:
                    state_writes.update(branch.effects.state_assignments)
                    state_conditions.update(branch.required_state_values)
                    state_conditions.update({
                        key: next(iter(values), "")
                        for key, values in branch.forbidden_state_values.items()
                    })
                invalid_state_keys = (
                    set(state_writes) | set(state_conditions)
                ) - set(ALLOWED_STATE_VALUES)
                invalid_state_values = {
                    key: value
                    for key, value in {**state_writes, **state_conditions}.items()
                    if key in ALLOWED_STATE_VALUES
                    and value not in ALLOWED_STATE_VALUES[key]
                }
                overlapping_flags = (
                    option.effects.open_flags & option.effects.close_flags
                )
                if unknown_fields:
                    raise ContentValidationError(
                        f"决策写入未知指标：{decision.decision_id}",
                        details={"fields": sorted(unknown_fields)},
                    )
                if unknown_ledger_fields:
                    raise ContentValidationError(
                        f"决策写入未知台账：{decision.decision_id}",
                        details={"fields": sorted(unknown_ledger_fields)},
                    )
                if unknown_ledger_condition_fields:
                    raise ContentValidationError(
                        f"决策条件读取未知台账：{decision.decision_id}",
                        details={"fields": sorted(unknown_ledger_condition_fields)},
                    )
                if unknown_flags:
                    raise ContentValidationError(
                        f"决策引用未注册旗标：{decision.decision_id}",
                        details={"flags": sorted(unknown_flags)},
                    )
                if unknown_condition_flags:
                    raise ContentValidationError(
                        f"决策选项条件引用未注册旗标：{decision.decision_id}",
                        details={"flags": sorted(unknown_condition_flags)},
                    )
                if invalid_state_keys or invalid_state_values:
                    raise ContentValidationError(
                        f"决策引用未登记多值状态：{decision.decision_id}",
                        details={
                            "fields": sorted(invalid_state_keys),
                            "values": invalid_state_values,
                        },
                    )
                if overlapping_flags:
                    raise ContentValidationError(
                        f"决策不能同时开启和关闭同一旗标：{decision.decision_id}",
                        details={"flags": sorted(overlapping_flags)},
                    )
                invalid_ranges = {
                    field_name: [minimum, maximum]
                    for field_name, (minimum, maximum) in (
                        option.effects.metric_deltas.items()
                    )
                    if minimum > maximum
                }
                if invalid_ranges:
                    raise ContentValidationError(
                        f"决策结算区间上下界颠倒：{decision.decision_id}",
                        details={"ranges": invalid_ranges},
                    )
            for block in (*decision.presentation_blocks, *decision.followup_blocks):
                if not block.block_id or not block.kind or not block.text.strip():
                    raise ContentValidationError(
                        f"决策后续文本字段不能为空：{decision.decision_id}"
                    )
                if block.block_id in block_ids:
                    raise ContentValidationError(
                        f"story block ID 重复：{block.block_id}"
                    )
                block_ids.add(block.block_id)
                unknown_origins = block.origin_ids - set(origins)
                if unknown_origins:
                    raise ContentValidationError(
                        f"决策后续文本引用未知出身：{block.block_id}",
                        details={"origins": sorted(unknown_origins)},
                    )

        for opportunity in opportunities:
            unknown_flags = (
                opportunity.requires_flags
                | opportunity.closes_on_flags
                | opportunity.completion_flags
                | opportunity.completion_effects.open_flags
                | opportunity.completion_effects.close_flags
            ) - registered_flags
            unknown_facts = (
                set(opportunity.allowed_fact_ids)
                | opportunity.completion_fact_ids
            ) - set(facts)
            if unknown_flags:
                raise ContentValidationError(
                    f"互动机会引用未知旗标：{opportunity.opportunity_id}",
                    details={"flags": sorted(unknown_flags)},
                )
            if unknown_facts:
                raise ContentValidationError(
                    f"互动机会引用未知事实：{opportunity.opportunity_id}",
                    details={"facts": sorted(unknown_facts)},
                )
            unknown_events = opportunity.requires_events - {
                item.event_id for item in events
            }
            if unknown_events:
                raise ContentValidationError(
                    f"互动机会引用未知事件：{opportunity.opportunity_id}",
                    details={"events": sorted(unknown_events)},
                )
            if (
                opportunity.completion_decision_id
                and opportunity.completion_decision_id not in decisions
            ):
                raise ContentValidationError(
                    f"互动机会引用未知完成决策：{opportunity.opportunity_id}",
                    details={"decision_id": opportunity.completion_decision_id},
                )
            unknown_effect_fields = (
                set(opportunity.completion_effects.metric_deltas)
                - allowed_effect_fields
            )
            unknown_ledger_fields = (
                set(opportunity.completion_effects.ledger_deltas)
                - allowed_ledger_fields
            )
            if (
                gameplay_schema_version >= 3
                and "budget_remaining"
                in opportunity.completion_effects.ledger_deltas
            ):
                raise ContentValidationError(
                    f"普通互动完成不得修改预算：{opportunity.opportunity_id}"
                )
            invalid_state_keys = (
                set(opportunity.completion_effects.state_assignments)
                - set(ALLOWED_STATE_VALUES)
            )
            invalid_state_values = {
                key: value
                for key, value in opportunity.completion_effects.state_assignments.items()
                if key in ALLOWED_STATE_VALUES
                and value not in ALLOWED_STATE_VALUES[key]
            }
            if unknown_effect_fields or unknown_ledger_fields:
                raise ContentValidationError(
                    f"互动完成结算引用未知字段：{opportunity.opportunity_id}",
                    details={
                        "metrics": sorted(unknown_effect_fields),
                        "ledger": sorted(unknown_ledger_fields),
                    },
                )
            if invalid_state_keys or invalid_state_values:
                raise ContentValidationError(
                    f"互动完成结算引用未登记多值状态：{opportunity.opportunity_id}",
                    details={
                        "fields": sorted(invalid_state_keys),
                        "values": invalid_state_values,
                    },
                )
            for block in opportunity.completion_blocks:
                if not block.block_id or not block.kind or not block.text.strip():
                    raise ContentValidationError(
                        f"互动完成文本字段不能为空：{opportunity.opportunity_id}"
                    )
                if block.block_id in block_ids:
                    raise ContentValidationError(
                        f"story block ID 重复：{block.block_id}"
                    )
                block_ids.add(block.block_id)
