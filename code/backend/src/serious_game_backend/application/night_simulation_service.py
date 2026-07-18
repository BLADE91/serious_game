from __future__ import annotations

from serious_game_backend.application.scripted_effect_service import ScriptedEffectService
from serious_game_backend.domain.game_session import GameSession
from serious_game_backend.domain.script_package import ScriptPackage


class NightSimulationService:
    def __init__(self, scripted_effects: ScriptedEffectService) -> None:
        self._scripted_effects = scripted_effects

    def run_night(self, session: GameSession, package: ScriptPackage) -> dict:
        day = session.game_state.story_day
        if any(item.get("story_day") == day for item in session.night_logs):
            return next(item for item in session.night_logs if item["story_day"] == day)
        beat = package.story_day(day)
        if beat is not None:
            self._scripted_effects.apply(
                session,
                package,
                beat.night_effects,
                source_id=f"night_d{day:02d}",
            )
            for index, branch in enumerate(beat.night_conditional_effects):
                if branch.matches(
                    session.flags,
                    session.state_values,
                    {
                        "signed_households": session.game_state.signed_households,
                        "reported_signed_households": session.game_state.reported_signed_households,
                        "budget_remaining": session.game_state.budget_remaining,
                    },
                ):
                    self._scripted_effects.apply(
                        session,
                        package,
                        branch.effects,
                        source_id=f"night_d{day:02d}:branch_{index}",
                    )
        visible_night_blocks = (
            [
                item
                for item in beat.night_blocks
                if item.is_visible(origin_id=session.origin_id, flags=session.flags)
            ]
            if beat is not None
            else []
        )
        record = {
            "story_day": day,
            "beat_id": beat.beat_id if beat else None,
            "lines": [item.text for item in visible_night_blocks],
            "summary": (
                visible_night_blocks[-1].text
                if visible_night_blocks
                else f"第{day}日夜间结转完成"
            ),
        }
        session.night_logs.append(record)
        session.logs.append({
            "type": "night_simulation",
            "story_day": day,
            "source_id": record["beat_id"],
            "visible_to_player": True,
        })
        return record
