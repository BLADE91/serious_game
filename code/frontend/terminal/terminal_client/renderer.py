from __future__ import annotations

from typing import Iterable


OPTION_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def render_feed(items: Iterable[dict]) -> list[str]:
    lines: list[str] = []
    for item in items:
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        speaker = item.get("speaker")
        if speaker:
            lines.append(f"{speaker}：{text}")
        else:
            lines.append(text)
    return lines


def render_state(state: dict) -> list[str]:
    story = state.get("story", {})
    ledger = state.get("ledger", {})
    points = ledger.get("action_points", {})
    signed = ledger.get("signed_households", {})
    budget = ledger.get("budget", {})
    indicators = state.get("indicators", {})
    lines = [
        (
            f"[D{story.get('day', '?')} / 第{story.get('chapter', '?')}章] "
            f"行动点 {points.get('remaining', '?')}/{points.get('daily_cap', '?')}｜"
            f"剩余 {ledger.get('days_left', '?')} 天"
        ),
        (
            f"签约 {signed.get('signed', '?')}/{signed.get('total', '?')} 户｜"
            f"预算 {budget.get('remaining', '?')} {budget.get('unit', '')}"
        ),
    ]
    if indicators:
        names = {
            "public_trust": "公众信任",
            "social_stability": "社会稳定",
            "political_credit": "政治信用",
            "media_pressure": "舆情压力",
            "cadre_discontent": "干部情绪",
        }
        values = [f"{names.get(key, key)}：{value}" for key, value in indicators.items()]
        lines.append("｜".join(values))
    if state.get("status") != "active":
        lines.append("本局已经结束。")
    return lines


def render_decision(
    pending: dict | None, *, show_options: bool = True
) -> tuple[list[str], dict[str, str]]:
    if not pending:
        return [], {}
    lines = [f"【{pending.get('title', '必须决策')}】", str(pending.get("text", ""))]
    labels: dict[str, str] = {}
    input_kind = pending.get("input_kind", "choice")
    schema = pending.get("input_schema") or {}
    if not show_options:
        if input_kind == "allocation":
            lines.append(
                f"总额：{schema.get('total')} {schema.get('unit', '')}；各项必须全部分完。"
            )
        return lines, labels
    if input_kind == "allocation":
        labels_by_id = schema.get("labels", {})
        fields = schema.get("fields", [])
        lines.append(
            f"总额：{schema.get('total')} {schema.get('unit', '')}；四项必须全部分完。"
        )
        for index, field in enumerate(fields):
            lines.append(f"  {OPTION_LABELS[index]}. {labels_by_id.get(field, field)}")
        return lines, labels
    for index, option in enumerate(pending.get("options", [])):
        if index >= len(OPTION_LABELS):
            break
        label = OPTION_LABELS[index]
        option_id = str(option["option_id"])
        if option.get("available", True):
            labels[label] = option_id
            lines.append(f"  {label}. {option.get('text', '')}")
        else:
            lines.append(
                f"  {label}. {option.get('text', '')}［不可选："
                f"{option.get('unavailable_reason') or '条件不足'}］"
            )
    return lines, labels


def render_actions(document: dict) -> list[str]:
    lines = [f"行动目录（当前成本档：{document.get('cost_tier', '?')}）"]
    for item in document.get("actions", []):
        state = "可用" if item.get("available") else f"不可用：{item.get('unavailable_reason')}"
        opportunities = ", ".join(item.get("opportunity_ids", [])) or "无"
        lines.append(
            f"  {item.get('action_id')}｜{item.get('name')}｜"
            f"{item.get('cost_action_points')} 点｜{state}｜入口：{opportunities}"
        )
    return lines


def render_opportunities(document: dict) -> list[str]:
    items = document.get("opportunities", [])
    if not items:
        reason = document.get("blocked_reason")
        return [str(reason or "当前没有开放的 NPC 互动机会。")]
    lines = ["NPC 互动机会："]
    for item in items:
        lines.append(
            f"  {item.get('opportunity_id')}｜NPC {item.get('npc_id')}｜"
            f"行动 {item.get('action_id')}｜{item.get('cost_action_points')} 点"
        )
    return lines
