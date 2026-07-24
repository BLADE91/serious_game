"""内部权威状态到玩家可见 DTO 的唯一投影入口。"""

from __future__ import annotations

from serious_game_backend.domain.game_session import GameSession
from serious_game_backend.domain.script_package import MetricBand, ScriptPackage


VISIBLE_INDICATORS = (
    "public_trust",
    "social_stability",
    "political_credit",
    "media_pressure",
    "cadre_discontent",
)


class VisibleStateProjector:
    def project(self, session: GameSession, package: ScriptPackage) -> dict:
        state = session.game_state
        indicators = {
            key: self._label(package.metric_bands[key], getattr(state, key))
            for key in VISIBLE_INDICATORS
        }
        pending = None
        if session.pending_decision is not None:
            pending = {
                "event_instance_id": session.pending_decision.event_instance_id,
                "decision_id": session.pending_decision.decision_id,
                "option_ids": list(session.pending_decision.option_ids),
                "options": [
                    {
                        "option_id": item.option_id,
                        "text": item.text,
                        "available": item.available,
                        "unavailable_reason": item.unavailable_reason,
                    }
                    for item in session.pending_decision.options
                ],
                "title": session.pending_decision.visible_title,
                "text": session.pending_decision.visible_text,
                "input_kind": session.pending_decision.input_kind,
                "input_schema": session.pending_decision.input_schema,
            }
        return {
            "session_id": session.session_id,
            "state_version": session.state_version,
            "status": session.status.value,
            "story": {
                "day": state.story_day,
                "chapter": package.chapter_for(state.story_day),
                "cost_tier": package.action_cost_tier(state.story_day).value,
                "beat_id": session.story_beat_id,
                "origin": {
                    "origin_id": session.origin_id,
                    "title": package.origins[session.origin_id].title,
                },
            },
            "ledger": {
                "days_left": state.days_left,
                "action_points": {
                    "remaining": state.action_points,
                    "daily_cap": state.daily_action_point_cap,
                    "overtime_available": (
                        state.action_points == 0
                        and not state.overtime_used_today
                        and state.chapter_overtime_count < 3
                        and state.fatigue < 75
                    ),
                    "chapter_overtime_remaining": max(
                        0, 3 - state.chapter_overtime_count
                    ),
                },
                "signed_households": {
                    "signed": state.signed_households,
                    "total": state.total_households,
                    "batches": session.signing_batch_summary(),
                },
                "budget": {
                    "remaining": state.budget_remaining,
                    "base_authorized": state.budget_base_authorized,
                    "approved_adjustments": state.budget_approved_adjustments,
                    "committed": state.budget_committed,
                    "paid": state.budget_paid,
                    "precoord_suspense": state.budget_precoord_suspense,
                    "unit": state.budget_unit,
                },
                "fatigue": {
                    "label": self._fatigue_label(state.fatigue),
                },
            },
            "indicators": indicators,
            "pending_decision": pending,
            "active_conversation": (
                {
                    "conversation_id": session.active_conversation.conversation_id,
                    "opportunity_id": session.active_conversation.opportunity_id,
                    "npc_id": session.active_conversation.npc_id,
                    "turn_count": session.active_conversation.turn_count,
                }
                if session.active_conversation is not None else None
            ),
            "visible_events": [
                {
                    "event_id": item.event_id,
                    "story_day": item.story_day,
                    "title": item.title,
                    "summary": item.summary,
                }
                for item in session.visible_events[-20:]
            ],
            "ending": session.ending_result,
        }

    @staticmethod
    def _label(bands: tuple[MetricBand, ...], value: int) -> str:
        matches = [band.label for band in bands if band.minimum <= value <= band.maximum]
        if len(matches) != 1:
            raise ValueError(f"visible metric value {value} has no unique band")
        return matches[0]

    @staticmethod
    def _fatigue_label(value: int) -> str:
        if value >= 75:
            return "撑不住了"
        if value >= 50:
            return "有些吃力"
        if value >= 25:
            return "略显疲乏"
        return "精神尚可"
