from __future__ import annotations

from dataclasses import asdict, dataclass

from serious_game_backend.domain.script_package import ScriptPackage


@dataclass(frozen=True, slots=True)
class CoverageItem:
    coverage_id: str
    category: str
    source_id: str
    required_evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CoverageContract:
    counts: dict[str, int]
    items: tuple[CoverageItem, ...]
    invalid_items: tuple[str, ...]

    @property
    def required_evidence_ids(self) -> tuple[str, ...]:
        return tuple(
            f"{item.coverage_id}:{evidence_type}"
            for item in self.items
            for evidence_type in item.required_evidence
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "counts": dict(self.counts),
            "items": [asdict(item) for item in self.items],
            "invalid_items": list(self.invalid_items),
            "required_evidence_ids": list(self.required_evidence_ids),
        }


def _duplicates(values: list[str]) -> set[str]:
    seen: set[str] = set()
    return {value for value in values if value in seen or seen.add(value)}


def build_coverage_contract(package: ScriptPackage) -> CoverageContract:
    """Discover every published acceptance obligation from the loaded package."""

    items: list[CoverageItem] = []
    invalid: list[str] = []

    def add(category: str, source_id: str, *evidence: str) -> None:
        items.append(CoverageItem(
            coverage_id=f"{category}:{source_id}",
            category=category,
            source_id=source_id,
            required_evidence=tuple(evidence),
        ))

    story_days = sorted(package.story_days)
    if story_days != list(range(1, 91)):
        invalid.append("story_days:expected_contiguous_1_90")
    for story_day in story_days:
        add("story_day", str(story_day), "api", "browser")

    main_ending_ids = [item.ending_id for item in package.main_endings]
    sub_ending_ids = [item.sub_ending_id for item in package.sub_endings]
    for duplicate in sorted(_duplicates(main_ending_ids)):
        invalid.append(f"main_ending:{duplicate}:duplicate")
    for duplicate in sorted(_duplicates(sub_ending_ids)):
        invalid.append(f"sub_ending:{duplicate}:duplicate")
    for ending in package.main_endings:
        add("main_ending", ending.ending_id, "route", "browser")
        for sub_ending_id in ending.sub_ending_ids:
            if sub_ending_id not in sub_ending_ids:
                invalid.append(
                    f"main_ending:{ending.ending_id}:unknown_sub_ending:{sub_ending_id}"
                )
    for ending in package.sub_endings:
        add("sub_ending", ending.sub_ending_id, "route")
        if ending.main_ending_id not in main_ending_ids:
            invalid.append(
                f"sub_ending:{ending.sub_ending_id}:unknown_main_ending:"
                f"{ending.main_ending_id}"
            )

    archives = {item.archive_id: item for item in package.archive_investigations}
    opportunities = {
        item.opportunity_id: item for item in package.interaction_opportunities
    }
    actions = dict(package.resource_actions)
    acquisition_method_count = 0
    for fact_id, fact in sorted(package.facts.items()):
        add("fact", fact_id, "knowledge")
        if not fact.acquisition_methods:
            invalid.append(f"fact:{fact_id}:missing_acquisition_method")
        for method in fact.acquisition_methods:
            acquisition_method_count += 1
            route_type = str(method.get("route_type", ""))
            source_id = str(method.get("source_id", ""))
            unlock_day = int(method.get("unlock_day", 0))
            add(
                "fact_acquisition_method",
                f"{fact_id}:{route_type}:{source_id}",
                "acquisition",
            )
            if route_type == "archive":
                archive = archives.get(source_id)
                if archive is None:
                    invalid.append(f"fact:{fact_id}:unknown_archive:{source_id}")
                elif fact_id not in archive.result_fact_ids:
                    invalid.append(f"fact:{fact_id}:archive_does_not_grant:{source_id}")
                elif archive.unlock_day != unlock_day:
                    invalid.append(f"fact:{fact_id}:archive_day_mismatch:{source_id}")
            elif route_type == "conversation":
                opportunity = opportunities.get(source_id)
                if opportunity is None:
                    invalid.append(f"fact:{fact_id}:unknown_opportunity:{source_id}")
                elif fact_id not in (
                    set(opportunity.allowed_fact_ids)
                    | set(opportunity.completion_fact_ids)
                ):
                    invalid.append(
                        f"fact:{fact_id}:opportunity_does_not_grant:{source_id}"
                    )
                elif not opportunity.day_min <= unlock_day <= opportunity.day_max:
                    invalid.append(
                        f"fact:{fact_id}:opportunity_day_mismatch:{source_id}"
                    )
            elif route_type == "action":
                action = actions.get(source_id)
                if action is None or not action.enabled:
                    invalid.append(f"fact:{fact_id}:unknown_action:{source_id}")
                elif fact_id not in action.result_fact_ids:
                    invalid.append(f"fact:{fact_id}:action_does_not_grant:{source_id}")
                elif action.unlock_day != unlock_day:
                    invalid.append(f"fact:{fact_id}:action_day_mismatch:{source_id}")
            else:
                invalid.append(f"fact:{fact_id}:unsupported_route_type:{route_type}")

    for archive in package.archive_investigations:
        add("archive", archive.archive_id, "transaction")
    for opportunity in package.interaction_opportunities:
        add("interaction_opportunity", opportunity.opportunity_id, "conversation")
    npc_ids = {item.npc_id for item in package.npc_profiles}
    for npc in package.npc_profiles:
        add("npc", npc.npc_id, "visibility")
    for location in package.map_locations:
        add("map_location", location.location_id, "action", "browser")
    for household in package.households:
        add("household", household.household_id, "contract")
        if not household.household_id.strip():
            invalid.append("household:missing_id")
        if household.representative_npc not in npc_ids:
            invalid.append(
                f"household:{household.household_id}:unknown_representative:"
                f"{household.representative_npc}"
            )

    coverage_ids = [item.coverage_id for item in items]
    for duplicate in sorted(_duplicates(coverage_ids)):
        invalid.append(f"coverage:{duplicate}:duplicate")

    return CoverageContract(
        counts={
            "story_days": len(package.story_days),
            "main_endings": len(package.main_endings),
            "sub_endings": len(package.sub_endings),
            "facts": len(package.facts),
            "fact_acquisition_methods": acquisition_method_count,
            "archives": len(package.archive_investigations),
            "interaction_opportunities": len(package.interaction_opportunities),
            "npcs": len(package.npc_profiles),
            "map_locations": len(package.map_locations),
            "households": len(package.households),
        },
        items=tuple(items),
        invalid_items=tuple(sorted(set(invalid))),
    )
