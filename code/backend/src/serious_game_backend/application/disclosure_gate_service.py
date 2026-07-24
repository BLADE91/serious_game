from __future__ import annotations

from dataclasses import dataclass

from serious_game_backend.domain.game_session import GameSession
from serious_game_backend.domain.interaction_opportunity import InteractionOpportunity
from serious_game_backend.domain.script_package import ScriptPackage


@dataclass(frozen=True, slots=True)
class DisclosureGate:
    trust_tier: int
    trust_label: str
    allowed_fact_ids: tuple[str, ...]


class DisclosureGateService:
    LABELS = {
        1: "敌对封口",
        2: "戒备提防",
        3: "认可交谈",
        4: "交底托付",
    }

    def build(
        self,
        session: GameSession,
        package: ScriptPackage,
        opportunity: InteractionOpportunity,
        *,
        repeat_count: int = 0,
    ) -> DisclosureGate:
        state = session.npc_states[opportunity.npc_id]
        if state.trust_score is None:
            # 有限角色按剧情白名单说话，不套用数值信任。
            return DisclosureGate(4, "剧情许可", tuple(opportunity.allowed_fact_ids))
        penalty = 10 if session.game_state.fatigue >= 75 else (
            5 if session.game_state.fatigue >= 50 else 0
        )
        repeat_penalty = 15 if repeat_count >= 2 else 5 if repeat_count == 1 else 0
        effective = max(0, state.trust_score - penalty - repeat_penalty)
        tier = 1 if effective <= 25 else 2 if effective <= 50 else 3 if effective <= 75 else 4
        values: list[str] = []
        for fact_id in opportunity.allowed_fact_ids:
            fact = package.facts.get(fact_id)
            if fact is None:
                continue
            if fact.owner_npc_ids and opportunity.npc_id not in fact.owner_npc_ids:
                continue
            if fact.disclosure_tier > tier:
                continue
            if fact.disclosure_tier == 4 and state.chapter_disclosure_used:
                continue
            values.append(fact_id)
        return DisclosureGate(tier, self.LABELS[tier], tuple(values))
