from __future__ import annotations

from serious_game_backend.application.visible_state import VisibleStateProjector
from serious_game_backend.domain.game_session import GameSession
from serious_game_backend.domain.script_package import ScriptPackage


class ReviewService:
    def __init__(self, projector: VisibleStateProjector) -> None:
        self._projector = projector

    def build(self, session: GameSession, package: ScriptPackage) -> dict:
        decisions = [
            {
                "story_day": item["story_day"],
                "decision_id": item["decision_id"],
                "option_id": item["option_id"],
                "cost_action_points": 0,
                **(
                    {"parameters": item["parameters"]}
                    if item.get("parameters")
                    else {}
                ),
            }
            for item in session.logs
            if item.get("type") == "decision"
        ]
        actions = [
            {
                "story_day": item["story_day"],
                "action_id": item["action_id"],
                "cost_action_points": item["cost_action_points"],
            }
            for item in session.logs
            if item.get("type") in {"tool_action", "free_text_turn"}
        ]
        selected = {item["decision_id"] for item in decisions}
        triggered = session.triggered_events
        state = self._projector.project(session, package)
        return {
            "session_id": session.session_id,
            "status": session.status.value,
            "decision_timeline": decisions,
            "action_timeline": actions,
            "night_timeline": list(session.night_logs),
            "visible_events": state["visible_events"],
            "known_facts": [
                {
                    "fact_id": fact_id,
                    "title": package.facts[fact_id].title,
                }
                for fact_id in sorted(session.known_fact_ids)
                if fact_id in package.facts
            ],
            "ending": session.ending_result,
            "final_visible_state": state if session.ending_result else None,
            "untriggered_paths": {
                "decision_ids": [
                    item.content_id
                    for item in package.decision_catalog
                    if item.content_id not in selected
                ],
                "event_ids": [
                    item.content_id
                    for item in package.event_catalog
                    if item.content_id not in triggered
                ],
            },
        }
