from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import replace
from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT / "src"))

from serious_game_backend.application.npc_turn_service import NPCTurnService
from serious_game_backend.application.scripted_delta_resolver import ScriptedDeltaResolver
from serious_game_backend.application.state_delta_validator import StateDeltaValidator
from serious_game_backend.config import Settings
from serious_game_backend.domain.enums import AvailabilityMode, NPCStateTier
from serious_game_backend.domain.fact_markers import disclosure_markers_for
from serious_game_backend.domain.llm import RoleTurnContext
from serious_game_backend.domain.npc_state import NPCState
from serious_game_backend.infrastructure.llm.openai_compatible import (
    OpenAICompatibleRoleLLMGateway,
)
from serious_game_backend.infrastructure.repositories.memory import (
    InMemoryLLMCallAuditRepository,
)
from serious_game_backend.infrastructure.script_packages.file_loader import (
    FileScriptPackageLoader,
)


PLAYER_PROBE = (
    "我想听你以自己的身份说说：眼下这件事你最在意什么，"
    "你希望县长接下来怎么做？只说你现在方便说的，不要求你交出证据。"
)


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="M3 全角色真实 API 矩阵测试")
    value.add_argument("--start", type=int, default=0)
    value.add_argument("--limit", type=int, default=5)
    value.add_argument("--base-url", default="https://api.qianzhang-ai.cn/v1")
    value.add_argument("--probe", default=PLAYER_PROBE)
    value.add_argument(
        "--require-policy-boundary", action="store_true",
        help="要求回复明确承认政策数字尚未确定，且不得出现未配置单位报价",
    )
    return value


def main() -> None:
    args = parser().parse_args()
    api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("DASHSCOPE_API_KEY is required")
    package = FileScriptPackageLoader().load(
        BACKEND_ROOT / "content" / "packages" / "pkg_backend_dev_v1"
    )
    opportunities = [
        item for item in package.interaction_opportunities
        if item.availability_mode is not AvailabilityMode.CLOSED
    ]
    selected = opportunities[args.start : args.start + args.limit]
    settings = replace(
        Settings(environment="test", repository="memory"),
        role_llm_provider="openai_compatible",
        role_llm_base_url=args.base_url.rstrip("/"),
        role_llm_model="qwen3.6-plus",
        role_llm_timeout_seconds=45,
        role_llm_max_retries=2,
        role_llm_fallback_to_fake=False,
        role_llm_max_calls_per_day=100,
        role_llm_max_calls_per_session=1000,
        role_llm_max_tokens_per_session=2_000_000,
    )
    audits = InMemoryLLMCallAuditRepository()
    gateway = OpenAICompatibleRoleLLMGateway(settings, api_key, audits)
    service = NPCTurnService(
        gateway, StateDeltaValidator(ScriptedDeltaResolver())
    )
    profiles = {item.npc_id: item for item in package.npc_profiles}
    results = []
    for absolute_index, opportunity in enumerate(selected, start=args.start):
        profile = profiles[opportunity.npc_id]
        permitted = set(opportunity.allowed_fact_ids)
        context = RoleTurnContext(
            session_id="m3-live-role-matrix",
            account_id="m3-live-test",
            operation_id=f"m3-live-role-{absolute_index:03d}",
            npc_id=profile.npc_id,
            player_text=args.probe,
            story_day=opportunity.day_min,
            opportunity_id=opportunity.opportunity_id,
            allowed_fact_ids=opportunity.allowed_fact_ids,
            npc_name=profile.name,
            npc_state_tier=profile.state_tier.value,
            role_setting=profile.role_setting,
            prompt_template=package.role_turn_prompt,
            prompt_version=package.role_turn_prompt_version,
            allowed_fact_texts={
                fact_id: package.facts[fact_id].text
                for fact_id in opportunity.allowed_fact_ids
                if fact_id in package.facts
            },
            allowed_fact_markers=disclosure_markers_for(
                opportunity.allowed_fact_ids
            ),
            forbidden_fact_markers=tuple(
                fact.title for fact_id, fact in package.facts.items()
                if fact_id not in permitted and len(fact.title.strip()) >= 4
            ),
            visible_world_context={
                "player_identity": "李致远，云溪县县长",
                "story_day": opportunity.day_min,
                "story_title": (
                    package.story_day(opportunity.day_min).title
                    if package.story_day(opportunity.day_min) else ""
                ),
                "origin": package.origins["technical"].title,
                "known_facts": [],
            },
            player_reference_materials={
                "mission": package.public_briefing["mission"],
                "compensation_policy": package.public_briefing["compensation_policy"],
                "known_materials": [],
            },
        )
        if profile.state_tier is NPCStateTier.DEEP:
            npc_state = NPCState(
                npc_id=profile.npc_id,
                state_tier=profile.state_tier,
                availability_mode=opportunity.availability_mode,
                trust_score=50,
                attitude_score=profile.initial_attitude,
                anxiety_score=profile.initial_anxiety,
            )
        else:
            npc_state = NPCState(
                npc_id=profile.npc_id,
                state_tier=profile.state_tier,
                availability_mode=opportunity.availability_mode,
            )
        before = len(audits.list_for_session(context.session_id))
        try:
            turn = service.run(
                context,
                npc_state,
                random_seed=f"m3-role-{absolute_index:03d}",
            )
            status = "passed" if profile.role_setting.strip() else "configuration_gap"
            error = None
            result_data = {
                "portrait_state": turn.portrait_state,
                "attitude_delta": turn.attitude_delta,
                "anxiety_delta": turn.anxiety_delta,
                "disclosure_id": turn.disclosure_id,
                "dialogue_length": len(turn.dialogue),
                "dialogue_preview": turn.dialogue[:100],
                "policy_boundary_acknowledged": any(
                    marker in turn.dialogue
                    for marker in ("尚未", "还没", "没有定", "没定", "细则", "不能给", "不能报")
                ),
                "unconfigured_unit_quote_matches": re.findall(
                    r"(?:每平方米|每平米|每亩|签约奖励)[^。；\n]{0,24}?[0-9]+(?:\.[0-9]+)?",
                    turn.dialogue,
                ),
                "risk_notes": list(turn.risk_notes),
            }
            if args.require_policy_boundary and (
                not result_data["policy_boundary_acknowledged"]
                or result_data["unconfigured_unit_quote_matches"]
            ):
                status = "failed"
                error = {
                    "type": "PolicyBoundaryViolation",
                    "code": "UNCONFIGURED_POLICY_NUMBER",
                    "message": "角色未正确遵守未配置补偿数字边界",
                }
        except Exception as exc:
            status = "failed"
            error = {
                "type": type(exc).__name__,
                "code": getattr(exc, "code", None),
                "message": str(exc),
            }
            result_data = {}
        call_audits = audits.list_for_session(context.session_id)[before:]
        results.append({
            "index": absolute_index,
            "opportunity_id": opportunity.opportunity_id,
            "npc_id": profile.npc_id,
            "npc_name": profile.name,
            "state_tier": profile.state_tier.value,
            "role_setting_present": bool(profile.role_setting.strip()),
            "status": status,
            "error": error,
            "audit_statuses": [item.status for item in call_audits],
            "audit_providers": [item.provider for item in call_audits],
            "tokens": sum(
                item.input_tokens + item.output_tokens for item in call_audits
            ),
            "result": result_data,
        })
    print(json.dumps({
        "range": [args.start, args.start + len(selected)],
        "total_live_opportunities": len(opportunities),
        "results": results,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
