from __future__ import annotations

from dataclasses import replace

from serious_game_backend.application.scripted_delta_resolver import ScriptedDeltaResolver
from serious_game_backend.domain.errors import ContentValidationError
from serious_game_backend.domain.game_session import GameSession
from serious_game_backend.domain.game_state import SCORE_FIELDS
from serious_game_backend.domain.script_package import ScriptPackage
from serious_game_backend.domain.story import ScriptedEffects


class ScriptedEffectService:
    """只执行剧本包声明的硬结算；区间按 session seed 稳定解析。"""

    def __init__(self, resolver: ScriptedDeltaResolver) -> None:
        self._resolver = resolver

    def apply(
        self,
        session: GameSession,
        package: ScriptPackage,
        effects: ScriptedEffects,
        *,
        source_id: str,
    ) -> None:
        unknown_flags = (effects.open_flags | effects.close_flags) - package.registered_flags
        if unknown_flags:
            raise ContentValidationError(
                "硬结算引用未注册旗标",
                details={"flags": sorted(unknown_flags), "source_id": source_id},
            )

        allowed_state_values = {
            "lead_roster_disposition": {
                "未获取", "己方封存", "呈交上级", "交给记者", "被销毁"
            }
        }
        for key, value in effects.state_assignments.items():
            if key not in allowed_state_values or value not in allowed_state_values[key]:
                raise ContentValidationError(
                    "硬结算尝试写入未登记的多值状态",
                    details={"field": key, "value": value, "source_id": source_id},
                )

        updates: dict[str, int] = {}
        for field_name, (minimum, maximum) in effects.metric_deltas.items():
            if field_name not in SCORE_FIELDS:
                raise ContentValidationError(
                    "硬结算尝试写入未授权字段",
                    details={"field": field_name, "source_id": source_id},
                )
            delta = self._resolver.resolve(
                minimum,
                maximum,
                random_seed=session.random_seed,
                source_id=f"{source_id}:{field_name}",
            )
            current = getattr(session.game_state, field_name)
            updates[field_name] = max(0, min(100, current + delta))

        ledger_bounds = {
            "budget_remaining": (0, None),
            "signed_households": (0, session.game_state.total_households),
            "reported_signed_households": (0, session.game_state.total_households),
        }
        for field_name, (minimum, maximum) in effects.ledger_deltas.items():
            if field_name not in ledger_bounds:
                raise ContentValidationError(
                    "硬结算尝试写入未授权台账字段",
                    details={"field": field_name, "source_id": source_id},
                )
            delta = self._resolver.resolve(
                minimum,
                maximum,
                random_seed=session.random_seed,
                source_id=f"{source_id}:{field_name}",
            )
            lower, upper = ledger_bounds[field_name]
            value = max(lower, getattr(session.game_state, field_name) + delta)
            updates[field_name] = min(upper, value) if upper is not None else value

        attitude_group = {
            "蒋崇岳背书", "蒋崇岳默许", "蒋崇岳弃保", "蒋崇岳否决",
            "flag_jiang_endorses", "flag_jiang_acquiesces",
            "flag_jiang_abandons", "flag_jiang_veto",
        }
        opened_attitudes = effects.open_flags & attitude_group
        if len(opened_attitudes) > 1:
            raise ContentValidationError(
                "同一结算不能同时开启多个蒋崇岳立场",
                details={"flags": sorted(opened_attitudes), "source_id": source_id},
            )
        if updates:
            session.game_state = replace(session.game_state, **updates)
        session.flags.difference_update(effects.close_flags)
        if opened_attitudes:
            session.flags.difference_update(attitude_group - opened_attitudes)
        session.flags.update(effects.open_flags)
        session.state_values.update(effects.state_assignments)
        session.logs.append({
            "type": "scripted_effect",
            "source_id": source_id,
            "story_day": session.game_state.story_day,
            "authority": "script",
            "visible_to_player": False,
        })
