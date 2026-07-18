from __future__ import annotations

from serious_game_backend.application.interaction_opportunity_service import (
    InteractionOpportunityService,
)
from serious_game_backend.domain.game_session import GameSession
from serious_game_backend.domain.script_package import ScriptPackage


class MapService:
    def __init__(self, opportunities: InteractionOpportunityService) -> None:
        self._opportunities = opportunities

    def build(self, session: GameSession, package: ScriptPackage) -> dict:
        available = {
            item.opportunity_id
            for item in self._opportunities.list_available(session, package)
        }
        active_event_ids = {
            item.event_id
            for item in session.visible_events
            if item.story_day == session.game_state.story_day
        }
        locations = []
        for item in package.map_locations:
            if session.game_state.story_day < item.unlock_day:
                visual_state = "locked"
            elif not item.required_flags.issubset(session.flags):
                visual_state = "locked"
            elif set(item.linked_opportunity_ids) & available:
                visual_state = "available"
            elif set(item.linked_event_ids) & active_event_ids:
                visual_state = "event_active"
            else:
                visual_state = "known"
            locations.append({
                "location_id": item.location_id,
                "name": item.name,
                "description": item.description,
                "visual_state": visual_state,
                "opportunity_ids": [
                    value for value in item.linked_opportunity_ids if value in available
                ],
                "newly_unlocked": False,
            })
        return {
            "state_version": session.state_version,
            "story_day": session.game_state.story_day,
            "locations": locations,
        }
