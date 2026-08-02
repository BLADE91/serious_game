from __future__ import annotations

from dataclasses import replace

from serious_game_backend.application.scripted_effect_service import ScriptedEffectService
from serious_game_backend.domain.game_session import GameSession
from serious_game_backend.domain.script_package import ScriptPackage
from serious_game_backend.application.trust_derivation_service import TrustDerivationService
from serious_game_backend.application.story_flow_service import StoryFlowService


class NightSimulationService:
    def __init__(
        self,
        scripted_effects: ScriptedEffectService,
        trust_derivation: TrustDerivationService | None = None,
    ) -> None:
        self._scripted_effects = scripted_effects
        self._trust_derivation = trust_derivation or TrustDerivationService()

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
        if day == 75:
            self._scripted_effects.freeze_d75_roster(session, package)
        propagated: list[dict] = []
        edges = tuple(package.npc_relationships)
        for log in session.logs:
            if log.get("type") != "conversation_turn" or log.get("story_day") != day:
                continue
            source_id = log.get("npc_id")
            requested_targets = set(log.get("will_share_with", ()))
            if not requested_targets:
                continue
            for edge in edges:
                if edge.get("source_npc_id") != source_id:
                    continue
                target_id = str(edge.get("target_npc_id", ""))
                if target_id not in requested_targets:
                    continue
                target = session.npc_states.get(target_id)
                if target is None or target.attitude_score is None:
                    continue
                attitude_delta = int(edge.get("attitude_delta", 0))
                anxiety_delta = int(edge.get("anxiety_delta", 0))
                session.npc_states[target_id] = replace(
                    target,
                    attitude_score=max(0, min(100, target.attitude_score + attitude_delta)),
                    anxiety_score=max(0, min(100, target.anxiety_score + anxiety_delta)),
                )
                propagated.append({
                    "source_npc_id": source_id,
                    "target_npc_id": target_id,
                    "disclosure_id": log.get("disclosure_id"),
                })
        self._trust_derivation.apply(session, package)
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
            "lines": [StoryFlowService.public_text(item.text) for item in visible_night_blocks],
            "summary": (
                StoryFlowService.public_text(visible_night_blocks[-1].text)
                if visible_night_blocks
                else f"第{day}日夜间结转完成"
            ),
            "morning_card": self._morning_card(day, visible_night_blocks, propagated),
            "propagation_count": len(propagated),
        }
        session.night_logs.append(record)
        session.logs.append({
            "type": "night_simulation",
            "story_day": day,
            "source_id": record["beat_id"],
            "visible_to_player": True,
        })
        return record

    @staticmethod
    def _morning_card(day: int, visible_blocks, propagated: list[dict]) -> list[str]:
        lines = [StoryFlowService.public_text(item.text) for item in visible_blocks[:2]]
        if propagated:
            lines.append("昨夜，与你白天接触有关的消息在熟人圈里传开了。")
        if not lines:
            lines.append(f"D{day + 1} 清晨，专班完成了昨日材料结转，暂无新的正式通报。")
        return lines[:3]
