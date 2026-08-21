from __future__ import annotations

from dataclasses import replace
import secrets

from serious_game_backend.application.ports import RoleLLMGateway
from serious_game_backend.application.scripted_effect_service import ScriptedEffectService
from serious_game_backend.domain.game_session import GameSession
from serious_game_backend.domain.llm import NightAgentContext, NightAgentResult
from serious_game_backend.domain.conversation import ForcedGroupConversation
from serious_game_backend.domain.script_package import ScriptPackage
from serious_game_backend.domain.story import ScriptedEffects
from serious_game_backend.application.trust_derivation_service import TrustDerivationService
from serious_game_backend.application.story_flow_service import StoryFlowService
from serious_game_backend.application.night_turn_safety import (
    NightTurnSafetyError,
    validate_night_turn_result,
)
from serious_game_backend.domain.fact_markers import forbidden_fact_signatures
from serious_game_backend.domain.errors import (
    RoleLLMBudgetExceededError,
    RoleLLMResponseError,
    RoleLLMUnavailableError,
)


class NightSimulationService:
    def __init__(
        self,
        scripted_effects: ScriptedEffectService,
        trust_derivation: TrustDerivationService | None = None,
        night_llm: RoleLLMGateway | None = None,
    ) -> None:
        self._scripted_effects = scripted_effects
        self._trust_derivation = trust_derivation or TrustDerivationService()
        self._night_llm = night_llm

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
        agent_failures: list[dict] = []
        (
            agent_exchanges,
            contact_selections,
            contact_responses,
            private_audit,
        ) = self._run_agent_scenes(session, package, agent_failures)
        followup_decisions = self._create_followup_conversations(
            session, package, agent_exchanges, agent_failures
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
                if not edge.get("propagation_enabled", True):
                    continue
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
            "lines": [
                StoryFlowService.session_public_text(item.text, session)
                for item in visible_night_blocks
            ],
            "summary": (
                StoryFlowService.session_public_text(
                    visible_night_blocks[-1].text, session
                )
                if visible_night_blocks
                else f"第{day}日夜间结转完成"
            ),
            "morning_card": self._morning_card(
                day,
                visible_night_blocks,
                propagated,
                agent_exchanges,
                package_id=package.package_id,
            ),
            "propagation_count": len(propagated),
            "agent_exchanges": agent_exchanges,
            "contact_selections": contact_selections,
            "contact_responses": contact_responses,
            "followup_decisions": followup_decisions,
            "agent_failures": agent_failures,
            "private_audit": private_audit,
        }
        session.night_logs.append(record)
        session.logs.append({
            "type": "night_simulation",
            "story_day": day,
            "source_id": record["beat_id"],
            "visible_to_player": True,
        })
        return record

    def activate_next_group_conversation(self, session: GameSession) -> None:
        if (
            session.active_group_conversation is None
            and session.group_conversation_queue
        ):
            session.active_group_conversation = (
                session.group_conversation_queue.pop(0)
            )
            session.active_group_conversation.status = "active"

    def _create_followup_conversations(
        self,
        session: GameSession,
        package: ScriptPackage,
        exchanges: list[dict],
        failures: list[dict],
    ) -> list[dict]:
        if self._night_llm is None or not exchanges:
            return []
        profiles = {item.npc_id: item for item in package.npc_profiles}
        social_roles = package.npc_social_roles or {}
        forbidden_disclosure_markers = tuple(dict.fromkeys(
            str(marker)
            for scene in package.night_agent_scenes
            for marker in scene.get("hidden_fact_markers", ())
            if str(marker)
        ))
        participants = {
            npc_id
            for exchange in exchanges
            for npc_id in exchange.get("participant_ids", ())
        }
        decisions: list[dict] = []
        created: list[ForcedGroupConversation] = []
        for npc_id in sorted(participants):
            profile = profiles.get(npc_id)
            if profile is None:
                continue
            related_transcript = tuple(
                turn
                for exchange in exchanges
                if npc_id in exchange.get("participant_ids", ())
                for turn in exchange.get("transcript", ())
            )
            for social_role in social_roles.get(npc_id, ()):
                followup_type = (
                    "petition" if social_role == "crowd" else "cadre_meeting"
                )
                eligible = tuple(sorted(
                    other_id
                    for other_id in participants
                    if other_id != npc_id
                    and social_role in social_roles.get(other_id, ())
                ))
                if not eligible:
                    continue
                context = NightAgentContext(
                    session_id=session.session_id,
                    account_id=session.account_id,
                    operation_id=(
                        f"night:{session.session_id}:"
                        f"{session.game_state.story_day}:followup:"
                        f"{followup_type}:{npc_id}"
                    ),
                    story_day=session.game_state.story_day,
                    scene_id=f"followup:{followup_type}",
                    phase="followup_initiation",
                    npc_id=npc_id,
                    npc_name=profile.name,
                    role_setting=profile.role_setting,
                    big_five=(
                        profile.big_five.as_dict() if profile.big_five else {}
                    ),
                    counterpart_ids=eligible,
                    transcript=related_transcript,
                    scene_goal=(
                        "决定是否需要在次日主动向县长提出群体诉求"
                        if followup_type == "petition"
                        else "决定是否需要在次日主动向县长汇报并会谈"
                    ),
                    allowed_followup_type=followup_type,
                    forbidden_disclosure_markers=forbidden_disclosure_markers,
                )
                result = self._safe_night_turn(
                    context,
                    failures,
                    forbidden_signatures=forbidden_fact_signatures(
                        package.facts, set(session.known_fact_ids)
                    ),
                )
                if result is None:
                    continue
                proposal = {
                    "initiator_npc_id": npc_id,
                    "model_id": result.model_id,
                    "followup_type": followup_type,
                    "initiate": result.initiate_followup,
                    "participant_ids": list(result.participant_ids),
                    "agenda": result.agenda,
                    "demands": list(result.demands),
                    "urgency": result.urgency,
                    "rationale": result.rationale,
                    "responses": [],
                    "created": False,
                }
                decisions.append(proposal)
                if (
                    not result.initiate_followup
                    or result.followup_type != followup_type
                    or npc_id not in result.participant_ids
                    or len(result.participant_ids) < 2
                    or not set(result.participant_ids).issubset(
                        {npc_id, *eligible}
                    )
                ):
                    continue
                accepted = [npc_id]
                for invited_id in result.participant_ids:
                    if invited_id == npc_id:
                        continue
                    invited = profiles[invited_id]
                    response_context = NightAgentContext(
                            session_id=session.session_id,
                            account_id=session.account_id,
                            operation_id=(
                                f"night:{session.session_id}:"
                                f"{session.game_state.story_day}:followup_response:"
                                f"{followup_type}:{npc_id}:{invited_id}"
                            ),
                            story_day=session.game_state.story_day,
                            scene_id=f"followup:{followup_type}",
                            phase="followup_response",
                            npc_id=invited_id,
                            npc_name=invited.name,
                            role_setting=invited.role_setting,
                            big_five=(
                                invited.big_five.as_dict()
                                if invited.big_five else {}
                            ),
                            counterpart_ids=(npc_id,),
                            transcript=related_transcript,
                            scene_goal=result.agenda,
                            forbidden_disclosure_markers=(
                                forbidden_disclosure_markers
                            ),
                    )
                    response = self._safe_night_turn(
                        response_context,
                        failures,
                        forbidden_signatures=forbidden_fact_signatures(
                            package.facts, set(session.known_fact_ids)
                        ),
                    )
                    if response is None:
                        proposal["responses"].append({
                            "npc_id": invited_id,
                            "model_id": None,
                            "response": "defer",
                            "rationale": "夜间联系未能完成，留待次日处理。",
                        })
                        continue
                    proposal["responses"].append({
                        "npc_id": invited_id,
                        "model_id": response.model_id,
                        "response": response.contact_response,
                        "rationale": response.rationale,
                    })
                    if response.contact_response == "accept":
                        accepted.append(invited_id)
                if len(accepted) < 2:
                    continue
                conversation = ForcedGroupConversation(
                    conversation_id=f"group_{secrets.token_hex(12)}",
                    conversation_type=followup_type,
                    initiator_npc_id=npc_id,
                    participant_ids=tuple(accepted),
                    agenda=result.agenda,
                    demands=result.demands,
                    urgency=result.urgency,
                    story_day=session.game_state.story_day + 1,
                )
                created.append(conversation)
                proposal["created"] = True
                break
        urgency_rank = {"critical": 0, "high": 1, "normal": 2, "none": 3}
        created.sort(key=lambda item: (
            0 if item.conversation_type == "petition" else 1,
            urgency_rank.get(item.urgency, 3),
            item.conversation_id,
        ))
        unique: list[ForcedGroupConversation] = []
        seen_groups: set[tuple[str, frozenset[str]]] = set()
        for item in created:
            key = (item.conversation_type, frozenset(item.participant_ids))
            if key in seen_groups:
                continue
            seen_groups.add(key)
            unique.append(item)
        session.group_conversation_queue.extend(unique)
        return decisions

    def _run_agent_scenes(
        self,
        session: GameSession,
        package: ScriptPackage,
        failures: list[dict],
    ) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
        if self._night_llm is None:
            return [], [], [], []
        profiles = {item.npc_id: item for item in package.npc_profiles}
        action_catalog = package.night_agent_actions or {}
        exchanges: list[dict] = []
        selections: list[dict] = []
        responses: list[dict] = []
        private_audit: list[dict] = []
        executed_global: set[str] = set()
        for scene in package.night_agent_scenes:
            if int(scene.get("story_day", -1)) != session.game_state.story_day:
                continue
            if not self._conditions_match(scene, session):
                continue
            candidate_ids = tuple(
                str(item)
                for item in scene.get(
                    "candidate_ids", scene.get("participant_ids", ())
                )
                if str(item) in profiles and str(item) in session.npc_states
            )
            if len(candidate_ids) < 2:
                continue
            scene_audit_start = len(private_audit)
            groups: list[tuple[str, ...]]
            if scene.get("selection_mode") == "autonomous":
                groups = self._select_contact_groups(
                    session,
                    scene,
                    candidate_ids,
                    profiles,
                    package,
                    action_catalog,
                    selections,
                    responses,
                    private_audit,
                    failures,
                )
            else:
                groups = [candidate_ids]
            scene_audits = private_audit[scene_audit_start:]
            if package.relationship_subnetworks and any(
                item.get("validation_verdict") == "rejected"
                for item in scene_audits
            ):
                exchanges.append(self._settle_hold_exchange(
                    session,
                    package,
                    scene,
                    tuple(dict.fromkeys(
                        str(item.get("npc_id"))
                        for item in scene_audits
                        if item.get("npc_id")
                    )),
                    action_catalog,
                    group_index=0,
                    executed_global=executed_global,
                    private_audit=scene_audits,
                ))
                continue
            for group_index, participants in enumerate(groups, start=1):
                exchanges.append(self._run_agent_exchange(
                    session,
                    package,
                    scene,
                    participants,
                    profiles,
                    action_catalog,
                    group_index=group_index,
                    executed_global=executed_global,
                    failures=failures,
                ))
                private_audit.extend(exchanges[-1].get("private_audit", ()))
        return exchanges, selections, responses, private_audit

    def _select_contact_groups(
        self,
        session: GameSession,
        scene: dict,
        candidate_ids: tuple[str, ...],
        profiles: dict,
        package: ScriptPackage,
        action_catalog: dict[str, dict],
        selections: list[dict],
        responses: list[dict],
        private_audit: list[dict],
        failures: list[dict],
    ) -> list[tuple[str, ...]]:
        max_contacts = max(0, int(scene.get("max_contacts_per_npc", 2)))
        groups: list[tuple[str, ...]] = []
        seen: set[frozenset[str]] = set()
        for npc_id in candidate_ids:
            profile = profiles[npc_id]
            candidates, allowed_topics, allowed_actions = self._actor_night_scope(
                session,
                package,
                scene,
                npc_id,
                candidate_ids,
                action_catalog,
            )
            context = NightAgentContext(
                session_id=session.session_id,
                account_id=session.account_id,
                operation_id=(
                    f"night:{session.session_id}:{session.game_state.story_day}:"
                    f"{scene['scene_id']}:contacts:{npc_id}"
                ),
                story_day=session.game_state.story_day,
                scene_id=str(scene["scene_id"]),
                phase="contact_selection",
                npc_id=npc_id,
                npc_name=profile.name,
                role_setting=profile.role_setting,
                big_five=profile.big_five.as_dict() if profile.big_five else {},
                counterpart_ids=candidates,
                scene_goal=str(scene.get("scene_goal", "")),
                private_context=str(
                    scene.get("private_contexts", {}).get(npc_id, "")
                ),
                allowed_actions=allowed_actions,
                allowed_topics=allowed_topics,
                forbidden_disclosure_markers=tuple(
                    scene.get("hidden_fact_markers", ())
                ),
                max_contacts=max_contacts,
                model_id=str(scene.get("model_ids", {}).get(npc_id, "")),
            )
            result = self._safe_night_turn(
                context,
                failures,
                forbidden_signatures=forbidden_fact_signatures(
                    package.facts, set(session.known_fact_ids)
                ),
            )
            if result is None:
                failure = self._failure_for_operation(
                    failures, context.operation_id
                )
                private_audit.append(self._proposal_audit(
                    phase="contact_selection",
                    npc_id=npc_id,
                    operation_id=context.operation_id,
                    original_proposal=failure.get("original_proposal"),
                    verdict="rejected",
                    reason=self._failure_reason(
                        failures, context.operation_id
                    ),
                    resolved_hard_outcome_ids=(
                        ["outcome_hold_position"]
                        if package.relationship_subnetworks else []
                    ),
                ))
                selections.append({
                    "scene_id": scene["scene_id"],
                    "npc_id": npc_id,
                    "model_id": None,
                    "contact_ids": [],
                    "rationale": "夜间联系选择未能完成。",
                    "accepted": False,
                })
                continue
            contacts = tuple(dict.fromkeys(
                item for item in result.contact_ids
                if item in candidates
            ))[:max_contacts]
            accepted = result.npc_id == npc_id and tuple(result.contact_ids) == contacts
            private_audit.append(self._proposal_audit(
                phase="contact_selection",
                npc_id=npc_id,
                operation_id=context.operation_id,
                original_proposal={
                    "npc_id": result.npc_id,
                    "contact_ids": list(result.contact_ids),
                    "rationale": result.rationale,
                },
                verdict="accepted" if accepted else "rejected",
                reason=None if accepted else "contact_not_one_hop_or_over_limit",
                model_id=result.model_id,
                resolved_hard_outcome_ids=(
                    ["outcome_hold_position"]
                    if package.relationship_subnetworks and not accepted else []
                ),
            ))
            selections.append({
                "scene_id": scene["scene_id"],
                "npc_id": npc_id,
                "model_id": result.model_id,
                "contact_ids": list(contacts) if accepted else [],
                "rationale": result.rationale,
                "accepted": accepted,
            })
            if not accepted or not contacts:
                continue
            accepted_contacts: list[str] = []
            for invited_id in contacts:
                invited = profiles[invited_id]
                response_context = NightAgentContext(
                    session_id=session.session_id,
                    account_id=session.account_id,
                    operation_id=(
                        f"night:{session.session_id}:"
                        f"{session.game_state.story_day}:{scene['scene_id']}:"
                        f"response:{npc_id}:{invited_id}"
                    ),
                    story_day=session.game_state.story_day,
                    scene_id=str(scene["scene_id"]),
                    phase="contact_response",
                    npc_id=invited_id,
                    npc_name=invited.name,
                    role_setting=invited.role_setting,
                    big_five=(
                        invited.big_five.as_dict() if invited.big_five else {}
                    ),
                    counterpart_ids=(npc_id,),
                    scene_goal=str(scene.get("scene_goal", "")),
                    private_context=str(
                        scene.get("private_contexts", {}).get(invited_id, "")
                    ),
                    forbidden_disclosure_markers=tuple(
                        scene.get("hidden_fact_markers", ())
                    ),
                    model_id=str(
                        scene.get("model_ids", {}).get(invited_id, "")
                    ),
                )
                response = self._safe_night_turn(
                    response_context,
                    failures,
                    forbidden_signatures=forbidden_fact_signatures(
                        package.facts, set(session.known_fact_ids)
                    ),
                )
                if response is None:
                    failure = self._failure_for_operation(
                        failures, response_context.operation_id
                    )
                    private_audit.append(self._proposal_audit(
                        phase="contact_response",
                        npc_id=invited_id,
                        operation_id=response_context.operation_id,
                        original_proposal=failure.get("original_proposal"),
                        verdict="rejected",
                        reason=self._failure_reason(
                            failures, response_context.operation_id
                        ),
                        resolved_hard_outcome_ids=(
                            ["outcome_hold_position"]
                            if package.relationship_subnetworks else []
                        ),
                    ))
                    responses.append({
                        "scene_id": scene["scene_id"],
                        "initiator_npc_id": npc_id,
                        "invited_npc_id": invited_id,
                        "model_id": None,
                        "response": "defer",
                        "rationale": "夜间邀请未能完成，默认延后处理。",
                        "accepted": False,
                    })
                    continue
                valid_response = (
                    response.npc_id == invited_id
                    and response.contact_response
                    in {"accept", "reject", "defer"}
                )
                if not valid_response:
                    private_audit.append(self._proposal_audit(
                        phase="contact_response",
                        npc_id=invited_id,
                        operation_id=response_context.operation_id,
                        original_proposal=self._night_result_document(response),
                        verdict="rejected",
                        reason="invalid_contact_response",
                        model_id=response.model_id,
                        resolved_hard_outcome_ids=(
                            ["outcome_hold_position"]
                            if package.relationship_subnetworks else []
                        ),
                    ))
                responses.append({
                    "scene_id": scene["scene_id"],
                    "initiator_npc_id": npc_id,
                    "invited_npc_id": invited_id,
                    "model_id": response.model_id,
                    "response": (
                        response.contact_response
                        if valid_response else "reject"
                    ),
                    "rationale": response.rationale,
                    "accepted": valid_response,
                })
                if valid_response and response.contact_response == "accept":
                    accepted_contacts.append(invited_id)
            if not accepted_contacts:
                continue
            member_set = frozenset((npc_id, *accepted_contacts))
            if any(member_set.issubset(existing) for existing in seen):
                continue
            superseded = {
                existing for existing in seen if existing < member_set
            }
            if superseded:
                seen.difference_update(superseded)
                groups = [
                    group for group in groups
                    if frozenset(group) not in superseded
                ]
            seen.add(member_set)
            groups.append(tuple(
                item for item in candidate_ids if item in member_set
            ))
        return groups

    def _run_agent_exchange(
        self,
        session: GameSession,
        package: ScriptPackage,
        scene: dict,
        participants: tuple[str, ...],
        profiles: dict,
        action_catalog: dict[str, dict],
        *,
        group_index: int,
        executed_global: set[str],
        failures: list[dict],
    ) -> dict:
            allowed = [
                action_catalog[action_id]
                for action_id in scene.get("action_ids", ())
                if action_id in action_catalog
                and self._conditions_match(action_catalog[action_id], session)
            ]
            transcript: list[dict] = []
            private_audit: list[dict] = []
            scene_blocked = False
            for round_index in range(1, int(scene.get("rounds", 2)) + 1):
                for npc_id in participants:
                    profile = profiles[npc_id]
                    one_hop, allowed_topics, actor_allowed = self._actor_night_scope(
                        session,
                        package,
                        scene,
                        npc_id,
                        participants,
                        action_catalog,
                    )
                    context = NightAgentContext(
                        session_id=session.session_id,
                        account_id=session.account_id,
                        operation_id=(
                            f"night:{session.session_id}:{session.game_state.story_day}:"
                            f"{scene['scene_id']}:{group_index}:dialogue:"
                            f"{round_index}:{npc_id}"
                        ),
                        story_day=session.game_state.story_day,
                        scene_id=str(scene["scene_id"]),
                        phase="dialogue",
                        npc_id=npc_id,
                        npc_name=profile.name,
                        role_setting=profile.role_setting,
                        big_five=(
                            profile.big_five.as_dict() if profile.big_five else {}
                        ),
                        counterpart_ids=one_hop,
                        transcript=tuple(transcript),
                        round_index=round_index,
                        scene_goal=str(scene.get("scene_goal", "")),
                        private_context=str(
                            scene.get("private_contexts", {}).get(npc_id, "")
                        ),
                        allowed_actions=actor_allowed,
                        allowed_topics=allowed_topics,
                        forbidden_disclosure_markers=tuple(
                            scene.get("hidden_fact_markers", ())
                        ),
                        model_id=str(scene.get("model_ids", {}).get(npc_id, "")),
                    )
                    result = self._safe_night_turn(
                        context,
                        failures,
                        forbidden_signatures=forbidden_fact_signatures(
                            package.facts, set(session.known_fact_ids)
                        ),
                    )
                    if result is None:
                        failure = self._failure_for_operation(
                            failures, context.operation_id
                        )
                        private_audit.append(self._proposal_audit(
                            phase="dialogue",
                            npc_id=npc_id,
                            operation_id=context.operation_id,
                            original_proposal=failure.get("original_proposal"),
                            verdict="rejected",
                            reason=self._failure_reason(
                                failures, context.operation_id
                            ),
                            resolved_hard_outcome_ids=(
                                ["outcome_hold_position"]
                                if package.relationship_subnetworks else []
                            ),
                        ))
                        scene_blocked = bool(package.relationship_subnetworks)
                        break
                    if result.npc_id != npc_id or not result.dialogue:
                        private_audit.append(self._proposal_audit(
                            phase="dialogue",
                            npc_id=npc_id,
                            operation_id=context.operation_id,
                            original_proposal=self._night_result_document(result),
                            verdict="rejected",
                            reason="invalid_dialogue_response",
                            model_id=result.model_id,
                            resolved_hard_outcome_ids=(
                                ["outcome_hold_position"]
                                if package.relationship_subnetworks else []
                            ),
                        ))
                        scene_blocked = bool(package.relationship_subnetworks)
                        break
                    transcript.append({
                        "round": round_index,
                        "speaker_npc_id": npc_id,
                        "speaker_name": profile.name,
                        "model_id": result.model_id,
                        "dialogue": result.dialogue,
                    })
                if scene_blocked:
                    break
            if scene_blocked:
                return self._settle_hold_exchange(
                    session,
                    package,
                    scene,
                    participants,
                    action_catalog,
                    group_index=group_index,
                    executed_global=executed_global,
                    private_audit=private_audit,
                    transcript=transcript,
                )
            proposals: list[dict] = []
            allowed_by_id = {str(item["action_id"]): item for item in allowed}
            for npc_id in participants:
                profile = profiles[npc_id]
                one_hop, allowed_topics, actor_allowed = self._actor_night_scope(
                    session,
                    package,
                    scene,
                    npc_id,
                    participants,
                    action_catalog,
                )
                context = NightAgentContext(
                    session_id=session.session_id,
                    account_id=session.account_id,
                    operation_id=(
                        f"night:{session.session_id}:{session.game_state.story_day}:"
                        f"{scene['scene_id']}:{group_index}:action:{npc_id}"
                    ),
                    story_day=session.game_state.story_day,
                    scene_id=str(scene["scene_id"]),
                    phase="action",
                    npc_id=npc_id,
                    npc_name=profile.name,
                    role_setting=profile.role_setting,
                    big_five=profile.big_five.as_dict() if profile.big_five else {},
                    counterpart_ids=one_hop,
                    transcript=tuple(transcript),
                    scene_goal=str(scene.get("scene_goal", "")),
                    private_context=str(
                        scene.get("private_contexts", {}).get(npc_id, "")
                    ),
                    allowed_actions=actor_allowed,
                    allowed_topics=allowed_topics,
                    forbidden_disclosure_markers=tuple(
                        scene.get("hidden_fact_markers", ())
                    ),
                    model_id=str(scene.get("model_ids", {}).get(npc_id, "")),
                )
                result = self._safe_night_turn(
                    context,
                    failures,
                    forbidden_signatures=forbidden_fact_signatures(
                        package.facts, set(session.known_fact_ids)
                    ),
                )
                if result is None:
                    failure = self._failure_for_operation(
                        failures, context.operation_id
                    )
                    failure_reason = self._failure_reason(
                        failures, context.operation_id
                    )
                    private_audit.append(self._proposal_audit(
                        phase="action",
                        npc_id=npc_id,
                        operation_id=context.operation_id,
                        original_proposal=failure.get("original_proposal"),
                        verdict="rejected",
                        reason=failure_reason,
                        resolved_hard_outcome_ids=(
                            ["outcome_hold_position"]
                            if package.relationship_subnetworks else []
                        ),
                    ))
                    if (
                        failure_reason == "hidden_fact_leakage"
                        and package.relationship_subnetworks
                    ):
                        scene_blocked = True
                        break
                    proposals.append({
                        "npc_id": npc_id,
                        "action_id": "night_hold_position",
                        "target_ids": [],
                        "topic_ids": [],
                        "accepted": True,
                        "fallback": True,
                        "reason": failure_reason,
                    })
                    continue
                action = next(
                    (
                        item for item in actor_allowed
                        if item["action_id"] == result.action_id
                    ),
                    None,
                )
                authoritative_action = (
                    action_catalog.get(str(result.action_id)) if action else None
                )
                valid_targets = (
                    set(authoritative_action.get("allowed_target_ids", ()))
                    if authoritative_action else set()
                )
                rejection_reason = None
                if result.npc_id != npc_id:
                    rejection_reason = "actor_not_eligible"
                elif action is None:
                    rejection_reason = "action_not_whitelisted"
                elif not set(result.target_ids).issubset(valid_targets):
                    rejection_reason = "target_not_allowed_for_action"
                elif not set(result.target_ids).issubset(one_hop):
                    rejection_reason = "target_not_one_hop"
                elif not set(result.topic_ids).issubset(allowed_topics):
                    rejection_reason = "topic_not_allowed"
                elif package.relationship_subnetworks and not result.topic_ids:
                    rejection_reason = "topic_required"
                if rejection_reason is not None:
                    private_audit.append(self._proposal_audit(
                        phase="action",
                        npc_id=npc_id,
                        operation_id=context.operation_id,
                        original_proposal={
                            "npc_id": result.npc_id,
                            "action_id": result.action_id,
                            "target_ids": list(result.target_ids),
                            "topic_ids": list(result.topic_ids),
                            "rationale": result.rationale,
                        },
                        verdict="rejected",
                        reason=rejection_reason,
                        model_id=result.model_id,
                        resolved_hard_outcome_ids=(
                            ["outcome_hold_position"]
                            if package.relationship_subnetworks else []
                        ),
                    ))
                    proposals.append({
                        "npc_id": npc_id,
                        "model_id": result.model_id,
                        "action_id": "night_hold_position",
                        "target_ids": [],
                        "topic_ids": [],
                        "accepted": True,
                        "fallback": True,
                        "reason": rejection_reason,
                    })
                    continue
                private_audit.append(self._proposal_audit(
                    phase="action",
                    npc_id=npc_id,
                    operation_id=context.operation_id,
                    original_proposal={
                        "npc_id": result.npc_id,
                        "action_id": result.action_id,
                        "target_ids": list(result.target_ids),
                        "topic_ids": list(result.topic_ids),
                        "rationale": result.rationale,
                    },
                    verdict="accepted",
                    reason=None,
                    model_id=result.model_id,
                ))
                proposals.append({
                    "npc_id": npc_id,
                    "model_id": result.model_id,
                    "action_id": result.action_id,
                    "target_ids": list(result.target_ids),
                    "topic_ids": list(result.topic_ids),
                    "rationale": result.rationale,
                    "accepted": True,
                })
            if scene_blocked:
                for audit in private_audit:
                    if (
                        audit["phase"] == "action"
                        and audit["validation_verdict"] == "accepted"
                    ):
                        audit.update({
                            "validation_verdict": "rejected",
                            "rejection_reason": "scene_hidden_fact_leakage",
                            "chosen_fallback": "night_hold_position",
                            "resolved_hard_outcome_ids": [
                                "outcome_hold_position"
                            ],
                        })
                return self._settle_hold_exchange(
                    session,
                    package,
                    scene,
                    participants,
                    action_catalog,
                    group_index=group_index,
                    executed_global=executed_global,
                    private_audit=private_audit,
                    transcript=transcript,
                )
            executed: list[str] = []
            resolved_hard_outcome_ids: list[str] = []
            accepted = [item for item in proposals if item["accepted"]]

            def fallback_selectors(
                selectors: list[dict], action_id: str, reason: str
            ) -> None:
                for proposal in selectors:
                    proposal.update({
                        "action_id": "night_hold_position",
                        "target_ids": [],
                        "topic_ids": [],
                        "fallback": True,
                        "reason": reason,
                    })
                for audit in private_audit:
                    if (
                        audit["validation_verdict"] == "accepted"
                        and audit["original_proposal"]
                        and audit["original_proposal"].get("action_id") == action_id
                    ):
                        audit.update({
                            "validation_verdict": "rejected",
                            "rejection_reason": reason,
                            "chosen_fallback": "night_hold_position",
                            "resolved_hard_outcome_ids": ["outcome_hold_position"],
                        })

            for action_id, action in allowed_by_id.items():
                selectors = [item for item in accepted if item["action_id"] == action_id]
                if action_id in executed_global:
                    fallback_selectors(selectors, action_id, "per_night_action_limit")
                    continue
                resolution = str(action.get("resolution", "unilateral"))
                required_actors = set(action.get("actor_ids", participants))
                participating_actors = required_actors & set(participants)
                should_execute = bool(selectors)
                if resolution == "consensus":
                    should_execute = (
                        required_actors.issubset(participants)
                        and {item["npc_id"] for item in selectors}
                        == participating_actors
                    )
                if not should_execute:
                    if (
                        package.relationship_subnetworks
                        and resolution == "consensus"
                        and selectors
                    ):
                        fallback_selectors(
                            selectors, action_id, "consensus_not_reached"
                        )
                    continue
                if len(executed) >= int(scene.get("max_action_executions", 1_000)):
                    fallback_selectors(selectors, action_id, "scene_execution_limit")
                    continue
                hard_outcome_ids = tuple(action.get("hard_outcome_ids", ()))
                if package.relationship_subnetworks:
                    outcomes = package.night_agent_hard_outcomes or {}
                    if not hard_outcome_ids or any(
                        outcome_id not in outcomes for outcome_id in hard_outcome_ids
                    ):
                        continue
                    for outcome_id in hard_outcome_ids:
                        outcome = outcomes[outcome_id]
                        self._scripted_effects.apply(
                            session,
                            package,
                            self._effects(outcome.get("effects", {})),
                            source_id=(
                                f"night_agent:{session.game_state.story_day}:"
                                f"{scene['scene_id']}:{outcome_id}"
                            ),
                        )
                        self._apply_npc_deltas(
                            session, outcome.get("npc_deltas", {})
                        )
                        resolved_hard_outcome_ids.append(outcome_id)
                else:
                    self._scripted_effects.apply(
                        session,
                        package,
                        self._effects(action.get("effects", {})),
                        source_id=(
                            f"night_agent:{session.game_state.story_day}:"
                            f"{scene['scene_id']}:{action_id}"
                        ),
                    )
                    self._apply_npc_deltas(session, action.get("npc_deltas", {}))
                executed.append(action_id)
                executed_global.add(action_id)
                for audit in private_audit:
                    if (
                        audit["validation_verdict"] == "accepted"
                        and audit["original_proposal"]
                        and audit["original_proposal"].get("action_id") == action_id
                    ):
                        audit["resolved_hard_outcome_ids"] = list(hard_outcome_ids)
            return {
                "scene_id": scene["scene_id"],
                "group_index": group_index,
                "participant_ids": list(participants),
                "transcript": transcript,
                "action_proposals": proposals,
                "executed_action_ids": executed,
                "resolved_hard_outcome_ids": resolved_hard_outcome_ids,
                "private_audit": private_audit,
                "public_summary": self._public_exchange_summary(
                    scene,
                    executed,
                    action_catalog,
                ),
            }

    def _settle_hold_exchange(
        self,
        session: GameSession,
        package: ScriptPackage,
        scene: dict,
        participants: tuple[str, ...],
        action_catalog: dict[str, dict],
        *,
        group_index: int,
        executed_global: set[str],
        private_audit: list[dict],
        transcript: list[dict] | None = None,
    ) -> dict:
        action_id = "night_hold_position"
        action = action_catalog.get(action_id, {})
        outcome_ids = tuple(action.get("hard_outcome_ids", ()))
        outcomes = package.night_agent_hard_outcomes or {}
        resolved = [
            outcome_id for outcome_id in outcome_ids if outcome_id in outcomes
        ]
        if action_id not in executed_global:
            for outcome_id in resolved:
                outcome = outcomes[outcome_id]
                self._scripted_effects.apply(
                    session,
                    package,
                    self._effects(outcome.get("effects", {})),
                    source_id=(
                        f"night_agent:{session.game_state.story_day}:"
                        f"{scene['scene_id']}:{outcome_id}"
                    ),
                )
                self._apply_npc_deltas(
                    session, outcome.get("npc_deltas", {})
                )
            executed_global.add(action_id)
        rejected = [
            item
            for item in private_audit
            if item.get("validation_verdict") == "rejected"
        ]
        proposals = [
            {
                "npc_id": item.get("npc_id"),
                "action_id": action_id,
                "target_ids": [],
                "topic_ids": [],
                "accepted": True,
                "fallback": True,
                "reason": item.get("rejection_reason"),
            }
            for item in rejected
        ]
        return {
            "scene_id": scene["scene_id"],
            "group_index": group_index,
            "participant_ids": list(participants),
            "transcript": list(transcript or ()),
            "action_proposals": proposals,
            "executed_action_ids": [action_id],
            "resolved_hard_outcome_ids": resolved,
            "private_audit": private_audit,
            "public_summary": self._public_exchange_summary(
                scene, [action_id], action_catalog
            ),
        }

    def _safe_night_turn(
        self,
        context: NightAgentContext,
        failures: list[dict],
        *,
        forbidden_signatures: dict[str, tuple[str, ...]],
    ):
        """Retry transient night-agent failures without blocking day settlement."""
        if self._night_llm is None:
            return None
        last_error: Exception | None = None
        attempts = 0
        for attempt in range(1, 3):
            attempts = attempt
            try:
                raw_result = self._night_llm.run_night_turn(context)
                try:
                    return validate_night_turn_result(
                        raw_result,
                        expected_npc_id=context.npc_id,
                        forbidden_fact_signatures=forbidden_signatures,
                        forbidden_markers=context.forbidden_disclosure_markers,
                    )
                except NightTurnSafetyError as exc:
                    # Never persist the rejected payload: it may itself contain
                    # the unauthorized fact that triggered this boundary.
                    exc.details["original_proposal"] = None
                    raise
            except (
                RoleLLMBudgetExceededError,
                RoleLLMResponseError,
                RoleLLMUnavailableError,
            ) as exc:
                last_error = exc
                if not getattr(exc, "retryable", False):
                    break
        failures.append({
            "scene_id": context.scene_id,
            "phase": context.phase,
            "npc_id": context.npc_id,
            "operation_id": context.operation_id,
            "attempts": attempts,
            "error_code": getattr(
                last_error, "code", type(last_error).__name__
            ),
            "message": str(last_error),
            "original_proposal": getattr(last_error, "details", {}).get(
                "original_proposal"
            ),
        })
        return None

    @staticmethod
    def _night_result_document(result: NightAgentResult) -> dict:
        return {
            "npc_id": result.npc_id,
            "model_id": result.model_id,
            "dialogue": result.dialogue,
            "action_id": result.action_id,
            "contact_ids": list(result.contact_ids),
            "contact_response": result.contact_response,
            "participant_ids": list(result.participant_ids),
            "agenda": result.agenda,
            "demands": list(result.demands),
            "target_ids": list(result.target_ids),
            "topic_ids": list(result.topic_ids),
            "rationale": result.rationale,
        }

    @staticmethod
    def _failure_for_operation(
        failures: list[dict], operation_id: str
    ) -> dict:
        return next(
            (
                item for item in reversed(failures)
                if item.get("operation_id") == operation_id
            ),
            {},
        )

    @classmethod
    def _failure_reason(
        cls, failures: list[dict], operation_id: str
    ) -> str:
        failure = cls._failure_for_operation(failures, operation_id)
        return (
            "hidden_fact_leakage"
            if failure.get("error_code") == "NIGHT_AGENT_HIDDEN_FACT_LEAKAGE"
            else "provider_failure"
        )

    def _actor_night_scope(
        self,
        session: GameSession,
        package: ScriptPackage,
        scene: dict,
        npc_id: str,
        candidate_ids: tuple[str, ...],
        action_catalog: dict[str, dict],
    ) -> tuple[tuple[str, ...], tuple[str, ...], tuple[dict, ...]]:
        """Project only one-hop contacts, topics and action descriptions to a model."""
        subnetworks = package.relationship_subnetworks or {}
        if not subnetworks:
            contacts = tuple(item for item in candidate_ids if item != npc_id)
            actions = tuple(
                self._public_action_candidate(action_catalog[action_id])
                for action_id in scene.get("action_ids", ())
                if action_id in action_catalog
                and self._conditions_match(action_catalog[action_id], session)
                and (
                    not action_catalog[action_id].get("actor_ids")
                    or npc_id in action_catalog[action_id]["actor_ids"]
                )
            )
            return contacts, tuple(scene.get("allowed_topics", ())), actions

        scene_subnetworks = set(scene.get("subnetwork_ids", ()))
        scene_actions = set(scene.get("action_ids", ()))
        scene_topics = set(scene.get("allowed_topics", ()))
        edges = [
            edge
            for edge in package.npc_relationships
            if edge.get("subnetwork") in scene_subnetworks
            and edge.get("source_npc_id") == npc_id
            and edge.get("target_npc_id") in candidate_ids
            and self._night_edge_active(edge, session)
        ]
        contact_set = {str(edge["target_npc_id"]) for edge in edges}
        contacts = tuple(item for item in candidate_ids if item in contact_set)
        edge_topics = {
            str(topic)
            for edge in edges
            for topic in edge.get("allowed_propagation_topics", ())
        }
        edge_actions = {
            str(action_id)
            for edge in edges
            for action_id in edge.get("night_action_ids", ())
        }
        subnetwork_topics = {
            str(topic)
            for subnetwork_id in scene_subnetworks
            for topic in subnetworks.get(subnetwork_id, {}).get(
                "allowed_propagation_topics", ()
            )
        }
        subnetwork_actions = {
            str(action_id)
            for subnetwork_id in scene_subnetworks
            for action_id in subnetworks.get(subnetwork_id, {}).get(
                "night_action_ids", ()
            )
        }
        allowed_topics = tuple(sorted(
            edge_topics & subnetwork_topics & scene_topics
        ))
        allowed_action_ids = edge_actions & subnetwork_actions & scene_actions
        allowed_actions = tuple(
            self._public_action_candidate(action_catalog[action_id])
            for action_id in scene.get("action_ids", ())
            if action_id in allowed_action_ids
            and action_id in action_catalog
            and self._conditions_match(action_catalog[action_id], session)
            and (
                not action_catalog[action_id].get("actor_ids")
                or npc_id in action_catalog[action_id]["actor_ids"]
            )
            and (
                not action_catalog[action_id].get("allowed_topics")
                or bool(
                    set(action_catalog[action_id]["allowed_topics"])
                    & set(allowed_topics)
                )
            )
        )
        return contacts, allowed_topics, allowed_actions

    @staticmethod
    def _public_action_candidate(action: dict) -> dict:
        return {
            key: action[key]
            for key in (
                "action_id",
                "name",
                "description",
                "allowed_target_ids",
                "allowed_topics",
            )
            if key in action
        }

    @classmethod
    def _night_edge_active(cls, edge: dict, session: GameSession) -> bool:
        day = session.game_state.story_day
        return (
            int(edge.get("active_from_day", 1)) <= day
            and day <= int(edge.get("active_until_day", 89))
            and cls._conditions_match(edge, session)
        )

    @staticmethod
    def _proposal_audit(
        *,
        phase: str,
        npc_id: str,
        operation_id: str,
        original_proposal: dict | None,
        verdict: str,
        reason: str | None,
        model_id: str | None = None,
        resolved_hard_outcome_ids: list[str] | None = None,
    ) -> dict:
        return {
            "phase": phase,
            "npc_id": npc_id,
            "original_proposal": original_proposal,
            "validation_verdict": verdict,
            "rejection_reason": reason,
            "chosen_fallback": (
                "night_hold_position" if verdict == "rejected" else None
            ),
            "resolved_hard_outcome_ids": list(resolved_hard_outcome_ids or ()),
            "model_audit_reference": f"{model_id or 'unavailable'}:{operation_id}",
        }

    @staticmethod
    def _conditions_match(rule: dict, session: GameSession) -> bool:
        required = set(rule.get("required_flags", ()))
        required_any = set(rule.get("required_any_flags", ()))
        forbidden = set(rule.get("forbidden_flags", ()))
        return (
            required.issubset(session.flags)
            and (not required_any or bool(required_any & session.flags))
            and not bool(forbidden & session.flags)
        )

    @staticmethod
    def _effects(value: dict) -> ScriptedEffects:
        def ranges(items: dict) -> dict[str, tuple[int, int]]:
            return {
                str(key): (
                    (int(item[0]), int(item[1]))
                    if isinstance(item, list) else (int(item), int(item))
                )
                for key, item in items.items()
            }
        return ScriptedEffects(
            metric_deltas=ranges(value.get("metric_deltas", {})),
            ledger_deltas=ranges(value.get("ledger_deltas", {})),
            open_flags=frozenset(value.get("open_flags", ())),
            close_flags=frozenset(value.get("close_flags", ())),
            state_assignments={
                str(key): str(item)
                for key, item in value.get("state_assignments", {}).items()
            },
        )

    @staticmethod
    def _apply_npc_deltas(session: GameSession, values: dict) -> None:
        for npc_id, delta in values.items():
            state = session.npc_states.get(npc_id)
            if state is None or state.attitude_score is None:
                continue
            session.npc_states[npc_id] = replace(
                state,
                attitude_score=max(
                    0, min(100, state.attitude_score + int(delta.get("attitude", 0)))
                ),
                anxiety_score=max(
                    0, min(100, state.anxiety_score + int(delta.get("anxiety", 0)))
                ),
            )

    @staticmethod
    def _public_exchange_summary(
        scene: dict,
        executed_action_ids: list[str],
        action_catalog: dict[str, dict],
    ) -> str:
        summaries = list(dict.fromkeys(
            str(action_catalog[action_id].get("public_direction_summary", "")).strip()
            for action_id in executed_action_ids
            if action_id in action_catalog
            and str(
                action_catalog[action_id].get("public_direction_summary", "")
            ).strip()
        ))
        if summaries:
            return "；".join(summaries)
        return str(scene.get("public_direction_summary", "")).strip()

    @staticmethod
    def _morning_card(
        day: int,
        visible_blocks,
        propagated: list[dict],
        agent_exchanges: list[dict],
        *,
        package_id: str,
    ) -> list[str]:
        if package_id == "pkg_gameplay_v3" and day == 29:
            observed = [
                StoryFlowService.public_text(item.text)
                for item in visible_blocks
                if item.presentation_phase == "morning"
            ]
            return observed[:3] or ["县城昨夜无事。"]
        # night_blocks 已在前一晚进入叙事 feed；晨间卡不再复制同一批文本。
        lines: list[str] = []
        if propagated:
            lines.append("昨夜，与你白天接触有关的消息在熟人圈里传开了。")
        summaries = list(dict.fromkeys(
            str(item.get("public_summary", "")).strip()
            for item in agent_exchanges
            if str(item.get("public_summary", "")).strip()
        ))
        if summaries:
            lines.append("夜间动向：" + "；".join(summaries))
        lines.append(f"D{day + 1} 清晨，专班完成了昨日材料结转。")
        return lines[:3]
