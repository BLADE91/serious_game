"""玩家白天行动服务。"""

from dataclasses import dataclass, replace
from typing import Any

from src.domain import ActionResult, GameActionRule, GameState, NPCState, SimulationLog


_NPC_SCORE_FIELDS = {"trust_to_player", "attitude_score", "anxiety_level", "granovetter_threshold"}
_GAME_SCORE_FIELDS = {"social_stability_index", "political_credit", "cadre_execution_index"}


@dataclass(frozen=True)
class DaytimeActionOutcome:
    """一次白天行动执行后的完整状态。"""

    game_state: GameState
    npc_states: list[NPCState]
    result: ActionResult


DEFAULT_ACTION_RULES: tuple[GameActionRule, ...] = (
    GameActionRule(
        action_id="household_visit",
        name="入户走访",
        cost_action_points=1,
        allowed_targets=["villager"],
        direct_payoff={
            "npc_delta": {
                "trust_to_player": 5,
                "attitude_score": 2,
                "anxiety_level": -2,
            },
            "append_known_info": ["第{day}天接受玩家入户走访，听到基础搬迁说明。"],
            "player_note": "获取该户基础态度信息。",
        },
        side_effects=["获取该户基础态度信息。"],
        risk_notes=["低信任状态下，目标给出的态度信息可能保留或失真。"],
    ),
    GameActionRule(
        action_id="cadre_private_talk",
        name="干部私谈",
        cost_action_points=1,
        allowed_targets=["cadre"],
        direct_payoff={
            "npc_delta": {
                "trust_to_player": 4,
                "anxiety_level": -2,
            },
            "game_delta": {
                "cadre_execution_index": 3,
            },
            "append_known_info": ["第{day}天与玩家私谈，交换了干部视角的信息。"],
            "player_note": "获取该干部视角下的执行阻力和风险判断。",
        },
        side_effects=["干部执行力小幅提升。"],
        risk_notes=["私下沟通依赖既有信任，信任不足时信息可能片面。"],
    ),
    GameActionRule(
        action_id="archive_review",
        name="调阅档案",
        cost_action_points=1,
        allowed_targets=["villager", "cadre", "external"],
        direct_payoff={
            "npc_delta": {
                "anxiety_level": 1,
            },
            "append_known_info": ["第{day}天留下了相关档案被调阅的记录。"],
            "player_note": "获取官方档案线索。",
        },
        side_effects=["获取土地台账、补偿方案或历史信访记录中的官方线索。"],
        risk_notes=["档案可能过时；调阅痕迹被知晓时，目标焦虑可能上升。"],
    ),
    GameActionRule(
        action_id="raise_compensation",
        name="提高补偿",
        cost_action_points=1,
        budget_cost=1000,
        allowed_targets=["villager"],
        direct_payoff={
            "npc_delta": {
                "trust_to_player": 8,
                "attitude_score": 5,
                "anxiety_level": -6,
                "reference_point": -1000,
            },
            "npc_set": {
                "core_demand_satisfied": True,
            },
            "player_note": "对目标户追加一档补偿让步。",
        },
        side_effects=["目标户核心诉求被暂时满足，预算减少。"],
        risk_notes=["差异化补偿一旦泄露，其他未签户的心理底价可能集体上移。"],
    ),
    GameActionRule(
        action_id="public_commitment",
        name="公开承诺",
        cost_action_points=1,
        allowed_targets=["villager", "cadre", "external"],
        direct_payoff={
            "npc_delta": {
                "trust_to_player": 6,
                "attitude_score": 4,
                "anxiety_level": -4,
            },
            "game_delta": {
                "political_credit": 2,
                "social_stability_index": 2,
            },
            "append_player_promises": ["第{day}天公开承诺：补偿、安置与程序信息可被追踪。"],
            "player_note": "形成可被追踪的公开承诺。",
        },
        side_effects=["政治信用和社会稳定预期小幅提升。"],
        risk_notes=["承诺若无法兑现，后续会造成更强的信任损失和政治信用损失。"],
    ),
)


class ActionService:
    """执行玩家白天行动的轻量规则服务。"""

    def __init__(self, rules: list[GameActionRule] | None = None) -> None:
        self._rules = list(rules) if rules is not None else list(DEFAULT_ACTION_RULES)
        self._rules_by_id = self._index_rules("action_id")
        self._rules_by_name = self._index_rules("name")

    def list_rules(self) -> list[GameActionRule]:
        """返回当前可执行的行动规则。"""

        return list(self._rules)

    def execute_action_text(
        self,
        game_state: GameState,
        npc_states: list[NPCState],
        action_text: str,
    ) -> DaytimeActionOutcome:
        """执行类似“入户走访 杨德清”的文本行动。"""

        action_name, target_text = self._parse_action_text(action_text)
        return self.execute_action(game_state, npc_states, action_name, target_text)

    def execute_action(
        self,
        game_state: GameState,
        npc_states: list[NPCState],
        action_name: str,
        target: str | NPCState | None,
    ) -> DaytimeActionOutcome:
        """执行一次结构化白天行动。"""

        rule = self._find_rule(action_name)
        npcs = list(npc_states)
        target_index, target_npc = self._find_target(npcs, target)
        self._validate_target(rule, target_npc)
        self._validate_resources(game_state, rule)

        updated_game_state = self._apply_game_updates(game_state, rule)
        updated_target = self._apply_npc_updates(target_npc, rule, game_state)
        updated_npcs = list(npcs)
        updated_npcs[target_index] = updated_target

        game_state_changes = self._describe_game_changes(game_state, updated_game_state, rule)
        npc_state_changes = {
            target_npc.npc_id: self._describe_npc_changes(target_npc, updated_target, rule)
        }
        budget_delta = updated_game_state.budget_remaining - game_state.budget_remaining
        risk_notes = self._risk_notes(rule, game_state, target_npc, updated_game_state)
        log = self._build_log(
            rule=rule,
            game_state=game_state,
            target_npc=target_npc,
            game_state_changes=game_state_changes,
            npc_state_changes=npc_state_changes,
            risk_notes=risk_notes,
        )

        return DaytimeActionOutcome(
            game_state=updated_game_state,
            npc_states=updated_npcs,
            result=ActionResult(
                action_name=rule.name,
                cost_action_points=rule.cost_action_points,
                budget_delta=budget_delta,
                game_state_changes=game_state_changes,
                npc_state_changes=npc_state_changes,
                risk_notes=risk_notes,
                logs=[log],
            ),
        )

    def _index_rules(self, key: str) -> dict[str, GameActionRule]:
        indexed: dict[str, GameActionRule] = {}
        for rule in self._rules:
            value = getattr(rule, key)
            if value in indexed:
                raise ValueError(f"duplicate action rule {key}: {value}")
            indexed[value] = rule
        return indexed

    def _parse_action_text(self, action_text: str) -> tuple[str, str | None]:
        text = action_text.strip()
        if not text:
            raise ValueError("action_text must not be empty")

        for rule in sorted(self._rules, key=lambda candidate: len(candidate.name), reverse=True):
            if text == rule.name:
                return rule.name, None
            if text.startswith(rule.name):
                target_text = text[len(rule.name) :].strip()
                if target_text:
                    return rule.name, target_text

        parts = text.split(maxsplit=1)
        if len(parts) == 1:
            return parts[0], None
        return parts[0], parts[1].strip()

    def _find_rule(self, action_name: str) -> GameActionRule:
        action_key = action_name.strip()
        if not action_key:
            raise ValueError("action_name must not be empty")

        rule = self._rules_by_name.get(action_key) or self._rules_by_id.get(action_key)
        if rule is None:
            supported_actions = "、".join(rule.name for rule in self._rules)
            raise ValueError(f"unsupported daytime action: {action_name}. Supported: {supported_actions}")
        return rule

    def _find_target(
        self,
        npc_states: list[NPCState],
        target: str | NPCState | None,
    ) -> tuple[int, NPCState]:
        if target is None:
            raise ValueError("daytime action requires a target NPC")

        if isinstance(target, NPCState):
            target_text = target.npc_id
        else:
            target_text = target.strip()

        if not target_text:
            raise ValueError("target NPC must not be empty")

        exact_matches = [
            (index, npc)
            for index, npc in enumerate(npc_states)
            if target_text in {npc.npc_id, npc.name}
        ]
        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            raise ValueError(f"ambiguous target NPC: {target_text}")

        fuzzy_matches = [
            (index, npc)
            for index, npc in enumerate(npc_states)
            if target_text in npc.name or target_text in npc.npc_id
        ]
        if len(fuzzy_matches) == 1:
            return fuzzy_matches[0]
        if len(fuzzy_matches) > 1:
            raise ValueError(f"ambiguous target NPC: {target_text}")
        raise ValueError(f"target NPC not found: {target_text}")

    def _validate_target(self, rule: GameActionRule, target_npc: NPCState) -> None:
        if not rule.allowed_targets:
            return

        target_keys = {target_npc.npc_type, target_npc.npc_id, target_npc.name}
        if target_keys.isdisjoint(rule.allowed_targets):
            allowed_targets = "、".join(rule.allowed_targets)
            raise ValueError(f"{rule.name} cannot target {target_npc.name}; allowed targets: {allowed_targets}")

    def _validate_resources(self, game_state: GameState, rule: GameActionRule) -> None:
        if game_state.action_points < rule.cost_action_points:
            raise ValueError(
                f"not enough action points for {rule.name}: "
                f"need {rule.cost_action_points}, have {game_state.action_points}"
            )
        if game_state.budget_remaining < rule.budget_cost:
            raise ValueError(
                f"not enough budget for {rule.name}: "
                f"need {rule.budget_cost}, have {game_state.budget_remaining}"
            )

    def _apply_game_updates(self, game_state: GameState, rule: GameActionRule) -> GameState:
        updates: dict[str, Any] = {
            "action_points": game_state.action_points - rule.cost_action_points,
            "budget_remaining": game_state.budget_remaining - rule.budget_cost,
        }
        for field_name, delta in rule.direct_payoff.get("game_delta", {}).items():
            current_value = getattr(game_state, field_name)
            updated_value = current_value + int(delta)
            if field_name in _GAME_SCORE_FIELDS:
                updated_value = _clamp_score(updated_value)
            updates[field_name] = updated_value

        for field_name, value in rule.direct_payoff.get("game_set", {}).items():
            updates[field_name] = value
        return replace(game_state, **updates)

    def _apply_npc_updates(
        self,
        target_npc: NPCState,
        rule: GameActionRule,
        game_state: GameState,
    ) -> NPCState:
        updates: dict[str, Any] = {}
        for field_name, delta in rule.direct_payoff.get("npc_delta", {}).items():
            current_value = getattr(target_npc, field_name)
            updated_value = current_value + int(delta)
            if field_name in _NPC_SCORE_FIELDS:
                updated_value = _clamp_score(updated_value)
            if field_name == "reference_point":
                updated_value = max(0, updated_value)
            updates[field_name] = updated_value

        for field_name, value in rule.direct_payoff.get("npc_set", {}).items():
            updates[field_name] = value

        known_info = self._formatted_items(rule, game_state, target_npc, "append_known_info")
        if known_info:
            updates["known_info"] = _append_unique(target_npc.known_info, known_info)

        player_promises = self._formatted_items(rule, game_state, target_npc, "append_player_promises")
        if player_promises:
            updates["player_promises"] = _append_unique(target_npc.player_promises, player_promises)

        if not updates:
            return target_npc
        return replace(target_npc, **updates)

    def _formatted_items(
        self,
        rule: GameActionRule,
        game_state: GameState,
        target_npc: NPCState,
        key: str,
    ) -> list[str]:
        items = rule.direct_payoff.get(key, [])
        if isinstance(items, str):
            items = [items]
        return [
            item.format(
                day=game_state.day,
                action_name=rule.name,
                target_name=target_npc.name,
                target_id=target_npc.npc_id,
            )
            for item in items
        ]

    def _describe_game_changes(
        self,
        before: GameState,
        after: GameState,
        rule: GameActionRule,
    ) -> dict[str, Any]:
        fields = ["action_points", "budget_remaining"]
        fields.extend(rule.direct_payoff.get("game_delta", {}).keys())
        fields.extend(rule.direct_payoff.get("game_set", {}).keys())
        return _describe_changes(before, after, fields, include_unchanged={"budget_remaining"})

    def _describe_npc_changes(
        self,
        before: NPCState,
        after: NPCState,
        rule: GameActionRule,
    ) -> dict[str, Any]:
        fields = list(rule.direct_payoff.get("npc_delta", {}).keys())
        fields.extend(rule.direct_payoff.get("npc_set", {}).keys())
        if "append_known_info" in rule.direct_payoff:
            fields.append("known_info")
        if "append_player_promises" in rule.direct_payoff:
            fields.append("player_promises")
        return _describe_changes(before, after, fields)

    def _risk_notes(
        self,
        rule: GameActionRule,
        game_state: GameState,
        target_npc: NPCState,
        updated_game_state: GameState,
    ) -> list[str]:
        notes = list(rule.risk_notes)
        if target_npc.trust_to_player < 40 and rule.action_id in {"household_visit", "cadre_private_talk"}:
            notes.append("目标当前信任度低于 40，本次获取的信息可信度偏低。")
        if rule.budget_cost and updated_game_state.budget_remaining < 1000:
            notes.append("预算余量已接近底线，后续补偿谈判空间明显收窄。")
        if game_state.social_stability_index < 50 and rule.action_id == "public_commitment":
            notes.append("当前社会稳定指数低于 50，公开承诺可能被放大解读。")
        return _deduplicate(notes) or ["无明显新增风险。"]

    def _build_log(
        self,
        rule: GameActionRule,
        game_state: GameState,
        target_npc: NPCState,
        game_state_changes: dict[str, Any],
        npc_state_changes: dict[str, dict[str, Any]],
        risk_notes: list[str],
    ) -> SimulationLog:
        budget_text = f"，预算 -{rule.budget_cost}" if rule.budget_cost else "，预算不变"
        message = f"执行白天行动：{rule.name} -> {target_npc.name}；行动点 -{rule.cost_action_points}{budget_text}。"
        return SimulationLog(
            day=game_state.day,
            log_type="daytime_action",
            message=message,
            visible_to_player=True,
            related_npc_ids=[target_npc.npc_id],
            metadata={
                "action_id": rule.action_id,
                "player_note": rule.direct_payoff.get("player_note"),
                "side_effects": list(rule.side_effects),
                "risk_notes": risk_notes,
                "game_state_changes": game_state_changes,
                "npc_state_changes": npc_state_changes,
            },
        )


def _append_unique(existing: list[str], items: list[str]) -> list[str]:
    updated = list(existing)
    for item in items:
        if item not in updated:
            updated.append(item)
    return updated


def _clamp_score(value: int) -> int:
    return max(0, min(100, value))


def _describe_changes(
    before: Any,
    after: Any,
    fields: list[str],
    include_unchanged: set[str] | None = None,
) -> dict[str, Any]:
    include_unchanged = include_unchanged or set()
    changes: dict[str, Any] = {}
    for field_name in _deduplicate(fields):
        before_value = getattr(before, field_name)
        after_value = getattr(after, field_name)
        if before_value == after_value and field_name not in include_unchanged:
            continue

        change: dict[str, Any] = {
            "before": before_value,
            "after": after_value,
        }
        if type(before_value) is int and type(after_value) is int:
            change["delta"] = after_value - before_value
        changes[field_name] = change
    return changes


def _deduplicate(items: list[str]) -> list[str]:
    unique_items: list[str] = []
    for item in items:
        if item not in unique_items:
            unique_items.append(item)
    return unique_items
