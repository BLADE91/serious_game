from __future__ import annotations

from serious_game_backend.application.event_service import EventService
from serious_game_backend.application.fatigue import action_point_cap_for, settle_fatigue
from serious_game_backend.domain.enums import SessionStatus
from serious_game_backend.domain.errors import DecisionRequiredError, SessionEndedError
from serious_game_backend.domain.game_session import GameSession
from serious_game_backend.domain.script_package import ScriptPackage


class StoryClockService:
    def __init__(self, event_service: EventService) -> None:
        self._events = event_service

    def end_day(
        self,
        session: GameSession,
        package: ScriptPackage,
        *,
        active_rest: bool = False,
    ) -> list[str]:
        if session.status is not SessionStatus.ACTIVE:
            raise SessionEndedError("当前游戏已经结束")
        if session.pending_decision is not None:
            raise DecisionRequiredError("必须先处理当前决策")

        state = session.game_state
        if state.story_day >= 90:
            session.game_state = state.reset_for_day(
                story_day=90,
                days_left=0,
                action_point_cap=state.daily_action_point_cap,
                fatigue=state.fatigue,
                consecutive_full_load_days=state.consecutive_full_load_days,
                chapter_overtime_count=state.chapter_overtime_count,
            )
            session.status = SessionStatus.ENDED
            session.logs.append({
                "type": "ending_state_frozen",
                "story_day": 90,
                "visible_to_player": False,
            })
            return []

        next_day = state.story_day + 1
        next_days_left = max(0, state.days_left - 1)
        chapter_transition = package.chapter_for(next_day) != package.chapter_for(state.story_day)
        fatigue = settle_fatigue(
            current=state.fatigue,
            points_spent=state.points_spent_today,
            overtime_used=state.overtime_used_today,
            overtime_points=state.overtime_points_today,
            active_rest=active_rest,
            chapter_transition=chapter_transition,
        )
        if active_rest:
            consecutive = 0
        elif state.points_spent_today >= 8:
            consecutive = state.consecutive_full_load_days + 1
        else:
            consecutive = 0
        overtime_count = 0 if chapter_transition else state.chapter_overtime_count
        cap = action_point_cap_for(fatigue, consecutive)
        session.game_state = state.reset_for_day(
            story_day=next_day,
            days_left=next_days_left,
            action_point_cap=cap,
            fatigue=fatigue,
            consecutive_full_load_days=consecutive,
            chapter_overtime_count=overtime_count,
        )
        session.logs.append({
            "type": "day_advance",
            "from_day": state.story_day,
            "to_day": next_day,
            "visible_to_player": False,
        })
        triggered = self._events.trigger_fixed_events(session, package)
        if next_day == 90:
            session.logs.append({
                "type": "ending_anchor_reached",
                "story_day": 90,
                "visible_to_player": False,
            })
        return triggered
