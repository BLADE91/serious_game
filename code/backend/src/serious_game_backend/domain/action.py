from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from serious_game_backend.domain.enums import ActionCostTier, ActionInputMode


@dataclass(frozen=True, slots=True)
class ActionRule:
    action_id: str
    name: str
    category: str
    effect_type: str
    costs: dict[ActionCostTier, int]
    daily_cap: int | None = None
    half_day: bool = False
    hard_force: bool = False
    precondition_flags_any: tuple[str, ...] = ()

    def cost_for(self, tier: ActionCostTier) -> int:
        return self.costs[tier]


@dataclass(frozen=True, slots=True)
class ActionCommand:
    input_mode: ActionInputMode
    client_action_id: str
    state_version: int
    action_id: str | None = None
    opportunity_id: str | None = None
    player_text: str | None = None
    target_npc_id: str | None = None
    decision_id: str | None = None
    option_id: str | None = None
    ordered_option_ids: tuple[str, ...] = ()
    parameters: dict[str, Any] = field(default_factory=dict)
    retry: bool = False

    def canonical_payload(self) -> dict[str, Any]:
        # retry 是传输控制位，不属于业务请求；否则同一次可重试失败无法保持相同 hash。
        return {
            "input_mode": self.input_mode.value,
            "client_action_id": self.client_action_id,
            "state_version": self.state_version,
            "action_id": self.action_id,
            "opportunity_id": self.opportunity_id,
            "player_text": self.player_text,
            "target_npc_id": self.target_npc_id,
            "decision_id": self.decision_id,
            "option_id": self.option_id,
            "ordered_option_ids": list(self.ordered_option_ids),
            "parameters": self.parameters,
        }
