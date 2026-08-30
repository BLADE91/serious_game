from __future__ import annotations

from dataclasses import replace

from serious_game_backend.domain.game_session import GameSession
from serious_game_backend.domain.script_package import NPCDemandDefinition, ScriptPackage
from serious_game_backend.application.npc_relationship_service import (
    NPCRelationshipService,
)


DEMAND_STATUSES = {
    "unknown", "discovered", "acknowledged", "committed", "satisfied",
    "lawfully_refused", "breached", "expired",
}
FINAL_DEMAND_STATUSES = {"satisfied", "lawfully_refused", "breached", "expired"}
VISIBLE_METRICS = {
    "public_trust", "social_stability", "political_credit", "media_pressure",
    "cadre_discontent", "integrity", "fatigue", "corruption_evidence",
}


class NPCDemandService:
    """把剧本诉求投影为可持久化状态；不把资源或指标交给 LLM。"""

    @staticmethod
    def initialize(session: GameSession, package: ScriptPackage) -> None:
        for demand in package.npc_demands:
            session.npc_demand_states.setdefault(demand.demand_id, {
                "npc_id": demand.npc_id,
                "status": "unknown",
                "updated_day": session.game_state.story_day,
                "history": [],
            })
        NPCDemandService.sync(session, package)

    @staticmethod
    def sync(session: GameSession, package: ScriptPackage) -> None:
        for demand in package.npc_demands:
            state = session.npc_demand_states.setdefault(demand.demand_id, {
                "npc_id": demand.npc_id,
                "status": "unknown", "updated_day": session.game_state.story_day,
                "history": [],
            })
            state.setdefault("npc_id", demand.npc_id)
            status = str(state.get("status", "unknown"))
            if status not in DEMAND_STATUSES:
                state["status"] = "unknown"
                status = "unknown"
            if status == "unknown" and NPCDemandService._discovered(
                session, package, demand
            ):
                NPCDemandService.transition(
                    session, demand.demand_id, "discovered", reason="剧情与接触条件已满足"
                )
                status = "discovered"
            if status == "discovered" and NPCDemandService._was_contacted(
                session, demand.npc_id
            ):
                NPCDemandService.transition(
                    session, demand.demand_id, "acknowledged", reason="已通过正式接触确认"
                )
                status = "acknowledged"
            if status not in FINAL_DEMAND_STATUSES:
                expiry_day = demand.satisfy.get("expires_day")
                if expiry_day is not None and session.game_state.story_day > int(expiry_day):
                    NPCDemandService.transition(
                        session, demand.demand_id, "expired", reason="处置窗口已经结束"
                    )
                    NPCDemandService.apply_consequences(session, demand, "expired")
                    continue
                required_flags = set(demand.satisfy.get("required_flags", ()))
                if status == "committed" and (
                    (
                        required_flags
                        and required_flags.issubset(session.flags)
                    )
                    or NPCDemandService._signed_contract_fulfills(
                        session, demand
                    )
                ):
                    NPCDemandService.transition(
                        session, demand.demand_id, "satisfied", reason="剧情事实满足处置条件"
                    )
                    NPCDemandService.apply_consequences(session, demand, "satisfied")

    @staticmethod
    def transition(
        session: GameSession,
        demand_id: str,
        status: str,
        *,
        reason: str,
    ) -> None:
        if status not in DEMAND_STATUSES:
            raise ValueError("invalid NPC demand status")
        state = session.npc_demand_states[demand_id]
        previous = str(state.get("status", "unknown"))
        if previous == status:
            return
        state["status"] = status
        state["updated_day"] = session.game_state.story_day
        state.setdefault("history", []).append({
            "story_day": session.game_state.story_day,
            "from": previous,
            "to": status,
            "reason": reason,
        })
        session.logs.append({
            "type": "npc_demand_transition",
            "story_day": session.game_state.story_day,
            "demand_id": demand_id,
            "from": previous,
            "to": status,
            "visible_to_player": status != "unknown",
        })

    @staticmethod
    def apply_consequences(
        session: GameSession,
        demand: NPCDemandDefinition,
        status: str,
    ) -> dict[str, int]:
        state = session.npc_demand_states.setdefault(demand.demand_id, {
            "npc_id": demand.npc_id,
            "status": "unknown",
            "updated_day": session.game_state.story_day,
            "history": [],
        })
        applied = state.setdefault("consequences_applied", [])
        if status in applied:
            return {}
        values = dict(demand.consequences.get(f"on_{status}", {}))
        deltas: dict[str, int] = {}
        updates = {}
        for field, raw_delta in values.items():
            if field not in VISIBLE_METRICS:
                continue
            before = int(getattr(session.game_state, field))
            after = max(0, min(100, before + int(raw_delta)))
            updates[field] = after
            deltas[field] = after - before
        if updates:
            session.game_state = replace(session.game_state, **updates)
        applied.append(status)
        return deltas

    @staticmethod
    def public(session: GameSession, package: ScriptPackage) -> list[dict]:
        names = {item.npc_id: item.name for item in package.npc_profiles}
        resource_names = {
            str(item["resource_id"]): str(item.get("name", item["resource_id"]))
            for item in (package.governance_config or {}).get("resource_pools", ())
        }
        result = []
        if package.gameplay_schema_version >= 4:
            actionable_npc_ids = NPCRelationshipService.actionable_npc_ids(
                session, package
            )
        else:
            actionable_npc_ids = set()
        for demand in package.npc_demands:
            if (
                package.gameplay_schema_version >= 4
                and demand.npc_id not in actionable_npc_ids
            ):
                continue
            state = session.npc_demand_states.get(demand.demand_id, {})
            status = str(state.get("status", "unknown"))
            if status == "unknown":
                continue
            allowed_transitions = NPCDemandService.allowed_transitions(
                status,
                demand.legal_disposition,
                can_fulfill=NPCDemandService.can_fulfill(
                    session, package, demand
                ),
            )
            if status == "discovered" and not NPCDemandService._was_contacted(
                session, demand.npc_id
            ):
                allowed_transitions = []
            result.append({
                "demand_id": demand.demand_id,
                "npc_id": demand.npc_id,
                "npc_name": names.get(demand.npc_id, demand.npc_id),
                "title": demand.title,
                "category": demand.category,
                "description": demand.description,
                "legal_disposition": demand.legal_disposition,
                "status": status,
                "updated_day": state.get("updated_day"),
                "required_resources": [
                    {
                        "resource_id": str(item["resource_id"]),
                        "name": resource_names.get(
                            str(item["resource_id"]), str(item["resource_id"])
                        ),
                        "quantity": int(item.get("quantity", 1)),
                    }
                    for item in demand.commit.get("resources", ())
                ],
                "allowed_transitions": allowed_transitions,
            })
        return result

    @staticmethod
    def allowed_transitions(
        status: str,
        legal_disposition: str,
        *,
        can_fulfill: bool = False,
    ) -> list[str]:
        if status == "discovered":
            return ["acknowledged"]
        if status == "acknowledged":
            return (
                ["lawfully_refused"]
                if legal_disposition == "lawfully_refuse"
                else ["committed"]
            )
        if status == "committed":
            return (["satisfied"] if can_fulfill else []) + ["breached"]
        return []

    @staticmethod
    def can_fulfill(
        session: GameSession,
        package: ScriptPackage,
        demand: NPCDemandDefinition,
    ) -> bool:
        """仅接受可核验的旗标、合同或已到履约日的资源承诺。"""

        state = session.npc_demand_states.get(demand.demand_id, {})
        if str(state.get("status", "unknown")) != "committed":
            return False
        required_flags = set(demand.satisfy.get("required_flags", ()))
        if required_flags and required_flags.issubset(session.flags):
            return True
        if NPCDemandService._signed_contract_fulfills(session, demand):
            return True
        required = {
            str(item["resource_id"]): int(item.get("quantity", 1))
            for item in demand.commit.get("resources", ())
        }
        if not required:
            return False
        fulfilled: dict[str, int] = {}
        for reservation in session.resource_reservations:
            if (
                reservation.owner_type != "npc_demand"
                or reservation.owner_id != demand.demand_id
                or reservation.status not in {"committed", "delivered"}
            ):
                continue
            matured = (
                reservation.status == "delivered"
                or (
                    reservation.committed_day is not None
                    and reservation.committed_day < session.game_state.story_day
                )
            )
            if matured:
                fulfilled[reservation.resource_id] = (
                    fulfilled.get(reservation.resource_id, 0)
                    + reservation.quantity
                )
        return all(
            fulfilled.get(resource_id, 0) >= quantity
            for resource_id, quantity in required.items()
        )

    @staticmethod
    def _signed_contract_fulfills(
        session: GameSession,
        demand: NPCDemandDefinition,
    ) -> bool:
        state = session.npc_demand_states.get(demand.demand_id, {})
        linked_contract_id = str(state.get("linked_contract_id", ""))
        return any(
            contract.status == "signed"
            and (
                contract.signatory_npc_id == demand.npc_id
                or contract.contract_id == linked_contract_id
            )
            for contract in session.household_contracts.values()
        )

    @staticmethod
    def _discovered(
        session: GameSession,
        package: ScriptPackage,
        demand: NPCDemandDefinition,
    ) -> bool:
        rule = demand.discover
        if session.game_state.story_day < int(rule.get("min_day", 1)):
            return False
        if package.gameplay_schema_version >= 4:
            visible_npcs = NPCRelationshipService.actionable_npc_ids(
                session, package
            )
        else:
            visible_npcs = set(
                (package.governance_config or {}).get(
                    "initial_visible_npc_ids", ()
                )
            )
        if package.gameplay_schema_version < 4:
            for opportunity in package.interaction_opportunities:
                if opportunity.availability_mode.value == "closed":
                    continue
                if session.game_state.story_day < opportunity.day_min:
                    continue
                if not opportunity.requires_flags.issubset(session.flags):
                    continue
                if not opportunity.requires_events.issubset(session.triggered_events):
                    continue
                visible_npcs.add(opportunity.npc_id)
        if demand.npc_id not in visible_npcs:
            return False
        flags = set(rule.get("required_any_flags", ()))
        facts = set(rule.get("required_any_fact_ids", ()))
        if flags and not flags.intersection(session.flags):
            return False
        if facts and not facts.intersection(session.known_fact_ids):
            return False
        return True

    @staticmethod
    def _was_contacted(session: GameSession, npc_id: str) -> bool:
        if any(npc_id in item.target_ids for item in session.governance_actions.values()):
            return True
        return any(
            item.get("npc_id") == npc_id
            and item.get("type") in {"conversation_started", "conversation_turn"}
            for item in session.logs
        )
