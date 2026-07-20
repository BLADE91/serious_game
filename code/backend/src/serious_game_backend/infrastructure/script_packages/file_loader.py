from __future__ import annotations

import hashlib
import json
from pathlib import Path

from serious_game_backend.domain.action import ActionRule
from serious_game_backend.domain.enums import ActionCostTier, AvailabilityMode, NPCStateTier
from serious_game_backend.domain.errors import ContentValidationError
from serious_game_backend.domain.interaction_opportunity import InteractionOpportunity
from serious_game_backend.domain.script_package import (
    CalendarSegment,
    ContentCatalogEntry,
    EndingAppendixDefinition,
    FixedEventRule,
    MainEndingDefinition,
    MapLocationDefinition,
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
ON_DEMAND_SUPPORTING_RUNTIME = {"dp5_04_recovery", "dp5_05_recovery"}
ALLOWED_STATE_VALUES = {
    "lead_roster_disposition": {
        "未获取", "己方封存", "呈交上级", "交给记者", "被销毁"
    }
}


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
            status=str(manifest["status"]),
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

    @staticmethod
    def _load_profiles(document: dict) -> tuple[NPCProfileStub, ...]:
        return tuple(NPCProfileStub(
            npc_id=str(item["npc_id"]),
            name=str(item["name"]),
            state_tier=NPCStateTier(item["state_tier"]),
            profile_id=str(item.get("profile_id") or item["npc_id"]),
            initial_attitude=int(item.get("initial_attitude", 50)),
            initial_anxiety=int(item.get("initial_anxiety", 50)),
            role_setting=str(item.get("role_setting", "")),
            source_line=int(item.get("source_line", 0)),
        ) for item in document.get("npcs", []))

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
            )
        return result

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
        *,
        status: str,
    ) -> None:
        if len(actions) != 31:
            raise ContentValidationError(f"行动规则必须正好 31 项，当前 {len(actions)}")
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
        if set(origins) != EXPECTED_ORIGINS:
            raise ContentValidationError(
                "开局出身必须完整定义五种固定类型",
                details={"actual": sorted(origins)},
            )
        for origin in origins.values():
            if not origin.title.strip() or not origin.description.strip():
                raise ContentValidationError(f"出身字段不能为空：{origin.origin_id}")
        for fact in facts.values():
            if not fact.fact_id or not fact.title.strip() or not fact.text.strip():
                raise ContentValidationError(f"事实字段不能为空：{fact.fact_id}")
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
            if unknown_opportunities or unknown_flags or unknown_events:
                raise ContentValidationError(
                    f"地图地点引用悬空：{location.location_id}",
                    details={
                        "opportunities": sorted(unknown_opportunities),
                        "flags": sorted(unknown_flags),
                        "events": sorted(unknown_events),
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
