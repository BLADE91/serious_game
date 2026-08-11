from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
import math
import re
import secrets

from serious_game_backend.application.governance_initializer import (
    sync_known_facts_to_archives,
)
from serious_game_backend.application.input_review_service import (
    InputReviewService,
    input_rejection_message,
)
from serious_game_backend.application.npc_turn_service import NPCTurnService
from serious_game_backend.application.package_lock import require_locked_package
from serious_game_backend.application.resource_availability import (
    active_budget_holds,
    unencumbered_budget,
)
from serious_game_backend.application.ports import (
    GameSessionRepository,
    RoleLLMGateway,
    ScriptPackageRepository,
)
from serious_game_backend.application.visible_state import VisibleStateProjector
from serious_game_backend.domain.enums import AvailabilityMode, SessionStatus
from serious_game_backend.domain.errors import (
    ActionUnavailableError,
    InsufficientActionPointsError,
    NotFoundError,
    PermissionDeniedError,
    SessionEndedError,
    StateVersionConflictError,
)
from serious_game_backend.domain.game_session import GameSession
from serious_game_backend.domain.gameplay_governance import (
    BASE_ACTION_PERMISSIONS,
    AdministrativeDocument,
    ArchiveRecord,
    ContractBatch,
    ContractVersion,
    GovernanceActionRecord,
    HouseholdContract,
    MeetingRecord,
    ResourceReservation,
    governance_now_iso,
)
from serious_game_backend.domain.household_settlement import (
    HouseholdSettlementEntry,
)
from serious_game_backend.domain.llm import (
    GovernanceLLMContext,
    NightAgentContext,
    RoleTurnContext,
)
from serious_game_backend.domain.script_package import (
    HouseholdDefinition,
    ScriptPackage,
)


class GameplayGovernanceService:
    """四项基础行动、正式文件和逐户合同的最小权威闭环。"""

    _RESOURCE_AUTHORITY_CLAUSE = (
        "资源与金额仅以结构化决议附件为准，正文新增表述不产生资源承诺。"
    )
    _STRUCTURED_NUMBER_PATTERN = re.compile(
        r"(?<![A-Za-z0-9_])\d+(?:\.\d+)?(?![A-Za-z0-9_])"
    )

    def __init__(
        self,
        sessions: GameSessionRepository,
        packages: ScriptPackageRepository,
        gateway: RoleLLMGateway,
        npc_turns: NPCTurnService,
        projector: VisibleStateProjector,
        input_review: InputReviewService,
    ) -> None:
        self._sessions = sessions
        self._packages = packages
        self._gateway = gateway
        self._npc_turns = npc_turns
        self._projector = projector
        self._input_review = input_review

    def overview(self, *, account_id: str, session_id: str) -> dict:
        session, package = self._load(account_id, session_id)
        sync_known_facts_to_archives(session, package)
        config = package.governance_config or {}
        profiles = {item.npc_id: item for item in package.npc_profiles}
        visible_npc_ids = self._visible_governance_npc_ids(session, package)
        meeting_npc_ids = set(config.get("leadership_meeting_npc_ids", ()))
        meeting_npc_ids &= visible_npc_ids
        return {
            "state_version": session.state_version,
            "permissions": config.get("permissions", {}),
            "base_actions": self._base_actions(session, package),
            "governance_actions": [
                asdict(item)
                for item in session.governance_actions.values()
            ],
            "target_catalogs": {
                "household_representative": [
                    {
                        "target_id": npc_id,
                        "label": profiles[npc_id].name,
                    }
                    for npc_id in config.get(
                        "household_representative_npc_ids", ()
                    )
                    if npc_id in profiles and npc_id in visible_npc_ids
                ],
                "cadre": [
                    {
                        "target_id": npc_id,
                        "label": profiles[npc_id].name,
                    }
                    for npc_id in config.get("cadre_npc_ids", ())
                    if npc_id in profiles and npc_id in visible_npc_ids
                ],
                "meeting_participants": [
                    {
                        "target_id": item.npc_id,
                        "label": item.name,
                    }
                    for item in package.npc_profiles
                    if item.npc_id in meeting_npc_ids
                ],
            },
            "document_types": [
                {
                    "document_type": document_type,
                    **rules,
                }
                for document_type, rules in config.get(
                    "document_rules", {}
                ).items()
                if set(rules.get("required_countersign_ids", ())).issubset(
                    meeting_npc_ids
                )
            ],
            "archives": [
                self._public_archive(item)
                for item in session.archive_records.values()
                if item.status == "available"
            ],
            "documents": [
                self._public_document(item, session=session)
                for item in session.administrative_documents.values()
            ],
            "meetings": [
                self._public_meeting(item) for item in session.meetings.values()
            ],
            "contract_batches": [
                asdict(item) for item in session.contract_batches.values()
            ],
            "contracts": [
                self._public_contract(item)
                for item in session.household_contracts.values()
            ],
            "resources": self._resource_status(session, package),
            "resource_ledger": list(session.resource_ledger_entries),
        }

    def archive_detail(
        self,
        *,
        account_id: str,
        session_id: str,
        archive_id: str,
    ) -> dict:
        """Return the full text of an acquired archive after it has been read."""
        session, package = self._load(account_id, session_id)
        sync_known_facts_to_archives(session, package)
        archive = session.archive_records.get(archive_id)
        if archive is None or archive.status != "available":
            raise NotFoundError("档案不存在或尚未取得")
        if not archive.read_at_days:
            raise ActionUnavailableError("请先通过查阅档案行动阅读这份材料")
        return {
            "state_version": session.state_version,
            "archive": self._public_archive(archive, include_content=True),
        }

    def contract_detail(
        self,
        *,
        account_id: str,
        session_id: str,
        contract_id: str,
    ) -> dict:
        session, _package = self._load(account_id, session_id)
        contract = self._contract(session, contract_id)
        return {
            "state_version": session.state_version,
            "contract": self._public_contract(contract, include_text=True),
        }

    def start_action(
        self,
        *,
        account_id: str,
        session_id: str,
        state_version: int,
        action_kind: str,
        target_ids: tuple[str, ...] = (),
        topic: str = "",
        archive_ids: tuple[str, ...] = (),
        proposed_document_type: str | None = None,
        lead_npc_id: str | None = None,
    ) -> dict:
        session, package = self._load_mutable(
            account_id, session_id, state_version
        )
        config = package.governance_config or {}
        definitions = {
            str(item["action_id"]): item
            for item in config.get("base_actions", [])
        }
        definition = definitions.get(action_kind)
        if definition is None or action_kind not in BASE_ACTION_PERMISSIONS:
            raise ActionUnavailableError("不存在这项基础行动")
        active = next(
            (
                item for item in session.governance_actions.values()
                if item.status == "active"
            ),
            None,
        )
        if active is not None:
            raise ActionUnavailableError(
                "已有一项基础行动正在进行",
                details={"action_instance_id": active.action_instance_id},
            )
        if session.pending_decision is not None:
            raise ActionUnavailableError("必须先处理当前剧情决策")
        if session.active_conversation is not None:
            raise ActionUnavailableError("必须先结束当前单人会谈")
        if session.active_group_conversation is not None:
            raise ActionUnavailableError("必须先完成当前群组会谈")
        cost = int(definition["cost"])
        if session.game_state.action_points < cost:
            raise InsufficientActionPointsError(
                "当日行动点不足",
                details={
                    "required": cost,
                    "remaining": session.game_state.action_points,
                },
            )
        self._validate_action_targets(
            package,
            action_kind=action_kind,
            target_ids=target_ids,
            archive_ids=archive_ids,
            topic=topic,
            proposed_document_type=proposed_document_type,
            lead_npc_id=lead_npc_id,
            session=session,
        )
        action_instance_id = f"govact_{secrets.token_hex(10)}"
        action = GovernanceActionRecord(
            action_instance_id=action_instance_id,
            action_kind=action_kind,
            story_day=session.game_state.story_day,
            target_ids=target_ids,
            required_permissions=BASE_ACTION_PERMISSIONS[action_kind],
            topic=topic.strip(),
            archive_ids=archive_ids,
        )
        session.game_state = session.game_state.spend_action_points(
            f"governance:{action_kind}", cost
        )
        session.governance_actions[action_instance_id] = action
        result: dict = {
            "action": asdict(action),
            "cost_action_points": cost,
        }
        if action_kind == "inspect_archives":
            sync_known_facts_to_archives(session, package)
            records = []
            for archive_id in archive_ids:
                archive = session.archive_records[archive_id]
                if session.game_state.story_day not in archive.read_at_days:
                    archive.read_at_days.append(session.game_state.story_day)
                records.append(self._public_archive(archive, include_content=True))
            action.status = "completed"
            action.completed_at = governance_now_iso()
            action.result_ids.extend(archive_ids)
            result["archives"] = records
        elif action_kind == "leadership_meeting":
            meeting_id = f"meeting_{secrets.token_hex(10)}"
            decision_mode = self._meeting_decision_mode(
                package, proposed_document_type
            )
            meeting = MeetingRecord(
                meeting_id=meeting_id,
                action_instance_id=action_instance_id,
                story_day=session.game_state.story_day,
                topic=topic.strip(),
                participant_ids=target_ids,
                decision_mode=decision_mode,
                lead_npc_id=str(lead_npc_id),
                proposed_document_type=proposed_document_type,
            )
            session.meetings[meeting_id] = meeting
            action.result_ids.append(meeting_id)
            result["meeting"] = self._public_meeting(meeting)
        # Some action kinds (notably archive inspection) complete immediately.
        # Serialize only after their authoritative status/result IDs are final.
        result["action"] = asdict(action)
        self._commit(session, state_version)
        result["state_version"] = session.state_version
        result["visible_state"] = self._projector.project(session, package)
        return result

    def action_turn(
        self,
        *,
        account_id: str,
        session_id: str,
        state_version: int,
        action_instance_id: str,
        player_text: str,
    ) -> dict:
        session, package = self._load_mutable(
            account_id, session_id, state_version
        )
        action = session.governance_actions.get(action_instance_id)
        if action is None:
            raise NotFoundError("基础行动实例不存在")
        if action.status != "active" or action.action_kind not in {
            "household_visit", "cadre_interview",
        }:
            raise ActionUnavailableError("该行动当前不能继续对话")
        text = player_text.strip()
        if not text:
            raise ActionUnavailableError("发言不能为空")
        relevant, review_reason = self._input_review.review(
            session,
            operation_id=(
                f"{action_instance_id}:input-review:"
                f"{sum(item.get('speaker_type') == 'player' for item in action.transcript) + 1}"
            ),
            player_text=text,
            scene_goal=action.topic,
        )
        if not relevant:
            session.logs.append({
                "type": "unrelated_input_rejected",
                "scene_type": action.action_kind,
                "scene_id": action_instance_id,
                "story_day": session.game_state.story_day,
                "reason": review_reason,
                "visible_to_player": False,
            })
            self._commit(session, state_version)
            return {
                "state_version": session.state_version,
                "input_rejected": True,
                "message": input_rejection_message(review_reason),
                "replies": [],
                "acquired_archive_ids": [],
                "contract_batch_proposal": None,
            }
        profiles = {item.npc_id: item for item in package.npc_profiles}
        replies = []
        for npc_id in action.target_ids:
            profile = profiles[npc_id]
            npc_state = session.npc_states[npc_id]
            turn = self._npc_turns.run(
                RoleTurnContext(
                    session_id=session.session_id,
                    account_id=session.account_id,
                    operation_id=(
                        f"{action_instance_id}:turn:"
                        f"{len(action.transcript) + 1}:{npc_id}"
                    ),
                    npc_id=npc_id,
                    player_text=text,
                    story_day=session.game_state.story_day,
                    opportunity_id=action_instance_id,
                    allowed_fact_ids=tuple(sorted(session.known_fact_ids)),
                    npc_name=profile.name,
                    npc_state_tier=profile.state_tier.value,
                    role_setting=profile.role_setting,
                    big_five=(
                        profile.big_five.as_dict() if profile.big_five else {}
                    ),
                    prompt_template=package.role_turn_prompt,
                    prompt_version=package.role_turn_prompt_version,
                    allowed_fact_texts={
                        fact_id: package.facts[fact_id].text
                        for fact_id in session.known_fact_ids
                        if fact_id in package.facts
                    },
                    conversation_turn_count=sum(
                        item.get("speaker_type") == "player"
                        for item in action.transcript
                    ),
                    conversation_history=tuple(action.transcript),
                    conversation_opening=(
                        "县长正在入户走访。"
                        if action.action_kind == "household_visit"
                        else "县长正在进行干部访谈。"
                    ),
                    conversation_goal=action.topic,
                    visible_world_context={
                        "story_day": session.game_state.story_day,
                        "signed_households": session.game_state.signed_households,
                        "budget_remaining": session.game_state.budget_remaining,
                    },
                    player_reference_materials={
                        "available_archive_titles": [
                            item.title
                            for item in session.archive_records.values()
                            if item.status == "available"
                        ],
                    },
                ),
                npc_state,
                random_seed=session.random_seed,
            )
            if turn.input_relevance == "irrelevant":
                replies.append({
                    "npc_id": npc_id,
                    "npc_name": profile.name,
                    "text": turn.dialogue,
                    "input_relevance": "irrelevant",
                })
                continue
            if npc_state.attitude_score is not None:
                session.npc_states[npc_id] = replace(
                    npc_state,
                    attitude_score=max(
                        0, min(100, npc_state.attitude_score + turn.attitude_delta)
                    ),
                    anxiety_score=max(
                        0, min(100, npc_state.anxiety_score + turn.anxiety_delta)
                    ),
                )
            replies.append({
                "npc_id": npc_id,
                "npc_name": profile.name,
                "text": turn.dialogue,
                "input_relevance": "relevant",
            })
        if replies and all(
            reply["input_relevance"] == "irrelevant"
            for reply in replies
        ):
            self._commit(session, state_version)
            return {
                "state_version": session.state_version,
                "input_rejected": True,
                "message": input_rejection_message(review_reason),
                "replies": [],
                "acquired_archive_ids": [],
                "contract_batch_proposal": None,
            }
        action.transcript.append({
            "speaker_type": "player",
            "text": text,
            "visible_to": list(action.target_ids),
        })
        for reply in replies:
            action.transcript.append({
                "speaker_type": "npc",
                **reply,
            })
        acquired = self._acquire_archives_from_interaction(
            session, package, action, text
        )
        proposal = None
        if (
            action.action_kind == "household_visit"
            and len(action.target_ids) == 1
            and any(word in text for word in ("签约", "签合同", "拟合同", "发合同"))
        ):
            proposal = self._detect_and_create_contract_batch(
                session, package, action.target_ids[0], text
            )
        self._commit(session, state_version)
        return {
            "state_version": session.state_version,
            "input_rejected": False,
            "replies": replies,
            "acquired_archive_ids": acquired,
            "contract_batch_proposal": (
                asdict(proposal) if proposal is not None else None
            ),
            "visible_state": self._projector.project(session, package),
        }

    def finish_action(
        self,
        *,
        account_id: str,
        session_id: str,
        state_version: int,
        action_instance_id: str,
    ) -> dict:
        session, package = self._load_mutable(
            account_id, session_id, state_version
        )
        action = session.governance_actions.get(action_instance_id)
        if action is None:
            raise NotFoundError("基础行动实例不存在")
        if action.status != "active":
            raise ActionUnavailableError("基础行动已经结束")
        if action.action_kind == "leadership_meeting":
            meeting = next(
                (
                    item for item in session.meetings.values()
                    if item.action_instance_id == action_instance_id
                ),
                None,
            )
            if meeting is not None and meeting.status == "discussion":
                raise ActionUnavailableError("班子会议必须先形成决议或中止记录")
        action.status = "completed"
        action.completed_at = governance_now_iso()
        self._commit(session, state_version)
        return {
            "state_version": session.state_version,
            "action": asdict(action),
            "visible_state": self._projector.project(session, package),
        }

    def cancel_action(
        self,
        *,
        account_id: str,
        session_id: str,
        state_version: int,
        action_instance_id: str,
    ) -> dict:
        session, package = self._load_mutable(
            account_id, session_id, state_version
        )
        action = session.governance_actions.get(action_instance_id)
        if action is None:
            raise NotFoundError("基础行动实例不存在")
        if action.status != "active":
            raise ActionUnavailableError("只有进行中的基础行动可以中止")
        meeting = next(
            (
                item for item in session.meetings.values()
                if item.action_instance_id == action_instance_id
            ),
            None,
        )
        if meeting is not None and meeting.status == "discussion":
            meeting.status = "aborted"
            meeting.resolution = {
                "adopted": False,
                "failure_reason": "玩家中止会议",
            }
            meeting.resolved_at = governance_now_iso()
        action.status = "cancelled"
        action.completed_at = governance_now_iso()
        session.logs.append({
            "type": "governance_action_cancelled",
            "action_instance_id": action_instance_id,
            "action_kind": action.action_kind,
            "story_day": session.game_state.story_day,
            "visible_to_player": True,
        })
        self._commit(session, state_version)
        return {
            "state_version": session.state_version,
            "action": asdict(action),
            "meeting": (
                self._public_meeting(meeting) if meeting is not None else None
            ),
            "visible_state": self._projector.project(session, package),
        }

    def meeting_turn(
        self,
        *,
        account_id: str,
        session_id: str,
        state_version: int,
        meeting_id: str,
        player_text: str,
        addressed_npc_id: str | None = None,
    ) -> dict:
        session, package = self._load_mutable(
            account_id, session_id, state_version
        )
        meeting = self._meeting(session, meeting_id)
        if meeting.status != "discussion":
            raise ActionUnavailableError("会议已经结束讨论")
        if addressed_npc_id and addressed_npc_id not in meeting.participant_ids:
            raise ActionUnavailableError("点名对象不在参会名单中")
        text = player_text.strip()
        if not text:
            raise ActionUnavailableError("会议发言不能为空")
        relevant, review_reason = self._input_review.review(
            session,
            operation_id=(
                f"{meeting_id}:input-review:"
                f"{sum(item.get('speaker_type') == 'player' for item in meeting.transcript) + 1}"
            ),
            player_text=text,
            scene_goal=meeting.topic,
        )
        if not relevant:
            session.logs.append({
                "type": "unrelated_input_rejected",
                "scene_type": "leadership_meeting",
                "scene_id": meeting_id,
                "story_day": session.game_state.story_day,
                "reason": review_reason,
                "visible_to_player": False,
            })
            self._commit(session, state_version)
            return {
                "state_version": session.state_version,
                "meeting_id": meeting_id,
                "input_rejected": True,
                "message": input_rejection_message(review_reason),
                "replies": [],
                "transcript": meeting.transcript,
            }
        profiles = {item.npc_id: item for item in package.npc_profiles}
        meeting.transcript.append({
            "speaker_type": "player",
            "text": text,
            "addressed_npc_id": addressed_npc_id,
            "visible_to": list(meeting.participant_ids),
        })
        ordered = [meeting.lead_npc_id, *(
            npc_id for npc_id in meeting.participant_ids
            if npc_id != meeting.lead_npc_id
        )]
        replies = []
        for order_index, npc_id in enumerate(ordered):
            profile = profiles[npc_id]
            meeting_role = (
                "分管或牵头领导：先汇报事实、依据、方案和风险"
                if order_index == 0
                else "参会领导：在分管领导汇报后明确表示同意、反对或提出修改意见"
            )
            result = self._gateway.run_night_turn(NightAgentContext(
                session_id=session.session_id,
                account_id=session.account_id,
                operation_id=(
                    f"{meeting_id}:turn:{len(meeting.transcript)}:{npc_id}"
                ),
                story_day=session.game_state.story_day,
                scene_id=meeting_id,
                phase="player_group_dialogue",
                npc_id=npc_id,
                npc_name=profile.name,
                role_setting=profile.role_setting,
                big_five=(
                    profile.big_five.as_dict() if profile.big_five else {}
                ),
                counterpart_ids=tuple(
                    item for item in meeting.participant_ids if item != npc_id
                ),
                transcript=tuple(meeting.transcript),
                round_index=sum(
                    item.get("speaker_type") == "player"
                    for item in meeting.transcript
                ),
                scene_goal=f"{meeting.topic}。你的会议角色：{meeting_role}。",
                player_text=text,
            ))
            if result.dialogue:
                reply = {
                    "speaker_type": "npc",
                    "npc_id": npc_id,
                    "npc_name": profile.name,
                    "text": result.dialogue,
                    "model_id": result.model_id,
                    "meeting_role": "lead_report" if order_index == 0 else "member_position",
                }
                meeting.transcript.append(reply)
                replies.append(reply)
        self._commit(session, state_version)
        return {
            "state_version": session.state_version,
            "meeting_id": meeting_id,
            "input_rejected": False,
            "replies": replies,
            "transcript": meeting.transcript,
        }

    def resolve_meeting(
        self,
        *,
        account_id: str,
        session_id: str,
        state_version: int,
        meeting_id: str,
        adopt: bool,
        resolution: dict,
    ) -> dict:
        session, package = self._load_mutable(
            account_id, session_id, state_version
        )
        meeting = self._meeting(session, meeting_id)
        if meeting.status != "discussion":
            raise ActionUnavailableError("会议已经形成结果")
        if not meeting.transcript:
            raise ActionUnavailableError("会议尚未进行公开讨论")
        npc_response_ids = {
            str(item.get("npc_id"))
            for item in meeting.transcript
            if item.get("speaker_type") == "npc" and item.get("npc_id")
        }
        if not set(meeting.participant_ids).issubset(npc_response_ids):
            raise ActionUnavailableError("分管领导汇报和其他参会领导表态尚未完成")
        normalized = self._validate_resolution(
            session, package, meeting, resolution
        )
        profiles = {item.npc_id: item for item in package.npc_profiles}
        positions = {}
        for npc_id in meeting.participant_ids:
            profile = profiles[npc_id]
            result = self._gateway.run_governance_task(
                GovernanceLLMContext(
                    session_id=session.session_id,
                    account_id=session.account_id,
                    operation_id=f"{meeting_id}:position:{npc_id}",
                    story_day=session.game_state.story_day,
                    task="meeting_position",
                    actor_id=npc_id,
                    actor_name=profile.name,
                    actor_profile=profile.role_setting,
                    payload={
                        "topic": meeting.topic,
                        "resolution": normalized,
                        "transcript": meeting.transcript,
                    },
                )
            )
            positions[npc_id] = dict(result.data)
        passed, failure_reason = self._meeting_passed(
            meeting, adopt=adopt, positions=positions
        )
        meeting.positions = positions
        meeting.resolution = {
            **normalized,
            "adopted": passed,
            "failure_reason": failure_reason,
        }
        meeting.status = "resolved" if passed else "rejected"
        meeting.resolved_at = governance_now_iso()
        action = session.governance_actions[meeting.action_instance_id]
        action.status = "completed"
        action.completed_at = governance_now_iso()
        minutes_id = f"archive:meeting:{meeting_id}"
        session.archive_records[minutes_id] = ArchiveRecord(
            archive_id=minutes_id,
            category="会议纪要",
            title=f"{meeting.topic}会议纪要",
            content=json.dumps(
                {
                    "participants": meeting.participant_ids,
                    "transcript": meeting.transcript,
                    "positions": positions,
                    "resolution": meeting.resolution,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            source_type="meeting",
            source_id=meeting_id,
            acquired_day=session.game_state.story_day,
            acquired_via="leadership_meeting",
            evidence_level="E3",
            confidentiality="internal",
        )
        action.result_ids.append(minutes_id)
        document = None
        if passed and meeting.proposed_document_type:
            document = self._draft_document(session, package, meeting)
            action.result_ids.append(document.document_id)
        self._commit(session, state_version)
        return {
            "state_version": session.state_version,
            "passed": passed,
            "failure_reason": failure_reason,
            "meeting": self._public_meeting(meeting),
            "document": (
                self._public_document(document) if document is not None else None
            ),
        }

    def edit_document(
        self,
        *,
        account_id: str,
        session_id: str,
        state_version: int,
        document_id: str,
        content: str,
    ) -> dict:
        session, package = self._load_mutable(
            account_id, session_id, state_version
        )
        document = self._document(session, document_id)
        if document.status not in {"draft", "pending_countersign"}:
            raise ActionUnavailableError("当前文件状态不允许修改")
        text = content.strip()
        if not text:
            raise ActionUnavailableError("文件正文不能为空")
        document.content = text
        document.version += 1
        document.status = "draft"
        document.countersigned_by = ()
        document.updated_at = governance_now_iso()
        self._record_document_version(
            document,
            created_by="player",
            model_id="player-edit",
            change_summary="玩家提交行政文件修订稿。",
        )
        self._review_and_revise_document(
            session,
            package,
            document,
            review_stage="manual_edit_review",
        )
        self._commit(session, state_version)
        return {
            "state_version": session.state_version,
            "document": self._public_document(document),
        }

    def countersign_document(
        self,
        *,
        account_id: str,
        session_id: str,
        state_version: int,
        document_id: str,
        npc_id: str,
    ) -> dict:
        session, package = self._load_mutable(
            account_id, session_id, state_version
        )
        document = self._document(session, document_id)
        if document.status not in {"draft", "pending_countersign"}:
            raise ActionUnavailableError("当前文件状态不允许会签")
        if npc_id not in document.required_countersign_ids:
            raise PermissionDeniedError("该NPC不是本文件的必要会签人")
        if npc_id in document.countersigned_by:
            raise ActionUnavailableError("该NPC已经完成会签")
        if document.review_status != "pass":
            self._review_and_revise_document(
                session,
                package,
                document,
                review_stage="pre_countersign_review",
            )
        if document.review_status != "pass":
            document.status = "draft"
            document.updated_at = governance_now_iso()
            self._commit(session, state_version)
            return {
                "state_version": session.state_version,
                "accepted": False,
                "reason": "行政文书审校尚未通过，不能进入会签。",
                "document": self._public_document(document),
            }
        profile = next(
            item for item in package.npc_profiles if item.npc_id == npc_id
        )
        result = self._gateway.run_governance_task(
            GovernanceLLMContext(
                session_id=session.session_id,
                account_id=session.account_id,
                operation_id=f"{document_id}:countersign:{npc_id}:v{document.version}",
                story_day=session.game_state.story_day,
                task="meeting_position",
                actor_id=npc_id,
                actor_name=profile.name,
                actor_profile=profile.role_setting,
                payload={
                    "topic": f"会签文件：{document.title}",
                    "resolution": document.resolution_snapshot,
                    "document_text": document.content,
                },
            )
        )
        if result.data["position"] in {"oppose", "abstain"}:
            document.status = "pending_countersign"
            self._commit(session, state_version)
            return {
                "state_version": session.state_version,
                "accepted": False,
                "reason": result.data["reason"],
                "document": self._public_document(document),
            }
        document.countersigned_by = tuple((
            *document.countersigned_by,
            npc_id,
        ))
        document.status = (
            "approved"
            if set(document.required_countersign_ids).issubset(
                document.countersigned_by
            )
            else "pending_countersign"
        )
        document.updated_at = governance_now_iso()
        self._commit(session, state_version)
        return {
            "state_version": session.state_version,
            "accepted": True,
            "reason": result.data["reason"],
            "document": self._public_document(document),
        }

    def issue_document(
        self,
        *,
        account_id: str,
        session_id: str,
        state_version: int,
        document_id: str,
    ) -> dict:
        session, _package = self._load_mutable(
            account_id, session_id, state_version
        )
        document = self._document(session, document_id)
        if document.status != "approved":
            raise ActionUnavailableError("文件尚未完成必要会签")
        if document.review_status != "pass":
            raise ActionUnavailableError(
                "行政文书审校尚未通过，不能正式印发",
                details={
                    "review_status": document.review_status,
                    "review_summary": document.review_summary,
                },
            )
        self._validate_document_consistency(
            document, document.content, _package
        )
        document.content_hash = self._hash({
            "document_id": document.document_id,
            "version": document.version,
            "content": document.content,
            "resolution": document.resolution_snapshot,
        })
        document.status = "issued"
        document.issued_day = session.game_state.story_day
        document.updated_at = governance_now_iso()
        archive_id = f"archive:{document.document_id}:v{document.version}"
        document.archive_id = archive_id
        session.archive_records[archive_id] = ArchiveRecord(
            archive_id=archive_id,
            category="政策与红头文件",
            title=document.title,
            content=document.content,
            source_type="administrative_document",
            source_id=document.document_id,
            acquired_day=session.game_state.story_day,
            acquired_via="document_issue",
            evidence_level="E3",
            confidentiality="internal",
        )
        self._commit(session, state_version)
        return {
            "state_version": session.state_version,
            "document": self._public_document(document),
        }

    def publish_document(
        self,
        *,
        account_id: str,
        session_id: str,
        state_version: int,
        document_id: str,
        scope: tuple[str, ...],
    ) -> dict:
        session, package = self._load_mutable(
            account_id, session_id, state_version
        )
        document = self._document(session, document_id)
        if document.status not in {"issued", "published"}:
            raise ActionUnavailableError("只有已签发文件可以公示")
        normalized_scope = tuple(dict.fromkeys(
            item.strip() for item in scope if item.strip()
        ))
        if not normalized_scope:
            raise ActionUnavailableError("公示范围不能为空")
        allowed_scope = set(document.resolution_snapshot.get(
            "public_scope", document.public_scope
        ))
        if allowed_scope and not set(normalized_scope).issubset(allowed_scope):
            raise PermissionDeniedError("公示范围超出会议决议")
        first_publication = not document.publication_records
        if not first_publication:
            if session.game_state.action_points < 1:
                raise InsufficientActionPointsError("再次公示需要1点行动点")
            session.game_state = session.game_state.spend_action_points(
                "governance:publish_document", 1
            )
        document.publication_records.append({
            "story_day": session.game_state.story_day,
            "scope": list(normalized_scope),
            "kind": "initial" if first_publication else "supplemental",
        })
        document.public_scope = tuple(dict.fromkeys((
            *document.public_scope,
            *normalized_scope,
        )))
        document.status = "published"
        document.updated_at = governance_now_iso()
        if document.archive_id and document.archive_id in session.archive_records:
            session.archive_records[document.archive_id].confidentiality = "public"
        self._commit(session, state_version)
        return {
            "state_version": session.state_version,
            "cost_action_points": 0 if first_publication else 1,
            "document": self._public_document(document),
            "visible_state": self._projector.project(session, package),
        }

    def confirm_contract_batch(
        self,
        *,
        account_id: str,
        session_id: str,
        state_version: int,
        batch_id: str,
        confirmed: bool,
    ) -> dict:
        session, package = self._load_mutable(
            account_id, session_id, state_version
        )
        batch = session.contract_batches.get(batch_id)
        if batch is None:
            raise NotFoundError("合同批次不存在")
        if batch.status != "pending_confirmation":
            raise ActionUnavailableError("合同批次已经确认或取消")
        if not confirmed:
            batch.status = "cancelled"
            self._commit(session, state_version)
            return {
                "state_version": session.state_version,
                "batch": asdict(batch),
                "contracts": [],
            }
        contracts = []
        profiles = {item.npc_id: item for item in package.npc_profiles}
        for household_id in batch.household_ids:
            if any(
                item.household_id == household_id
                and item.status in {
                    "awaiting_terms", "draft", "under_review",
                    "accepted", "signed",
                }
                for item in session.household_contracts.values()
            ):
                raise ActionUnavailableError(
                    "批次内家庭已经存在未结或有效合同",
                    details={"household_id": household_id},
                )
            household = self._household(package, household_id)
            limited = package.limited_signatory_for(household_id)
            if limited is None:
                profile = profiles[household.representative_npc]
                signatory_name = profile.name
                signatory_npc_id = profile.npc_id
            else:
                signatory_name = limited.name
                signatory_npc_id = None
            contract_id = f"contract_{secrets.token_hex(10)}"
            contract = HouseholdContract(
                contract_id=contract_id,
                batch_id=batch_id,
                household_id=household_id,
                signatory_name=signatory_name,
                signatory_npc_id=signatory_npc_id,
                created_day=session.game_state.story_day,
            )
            session.household_contracts[contract_id] = contract
            contracts.append(contract)
        batch.contract_ids = tuple(item.contract_id for item in contracts)
        batch.status = "confirmed"
        batch.confirmed_at = governance_now_iso()
        self._commit(session, state_version)
        return {
            "state_version": session.state_version,
            "batch": asdict(batch),
            "contracts": [self._public_contract(item) for item in contracts],
        }

    def set_contract_terms(
        self,
        *,
        account_id: str,
        session_id: str,
        state_version: int,
        contract_id: str,
        term_sheet: dict,
    ) -> dict:
        session, package = self._load_mutable(
            account_id, session_id, state_version
        )
        contract = self._contract(session, contract_id)
        if contract.status not in {
            "awaiting_terms", "draft", "explanation_requested",
            "counteroffered", "rejected",
        }:
            raise ActionUnavailableError("当前合同状态不能重设资源条款")
        self._release_contract_reservations(
            session, contract_id, reason="terms_replaced"
        )
        normalized = self._validate_term_sheet(
            session, package, contract, term_sheet
        )
        result = self._gateway.run_governance_task(
            GovernanceLLMContext(
                session_id=session.session_id,
                account_id=session.account_id,
                operation_id=f"{contract_id}:draft:{len(contract.versions) + 1}",
                story_day=session.game_state.story_day,
                task="draft_contract",
                actor_id="contract_writer",
                actor_name="合同文书模型",
                actor_profile="只负责把已校验资源条款转写为合同，不得增加承诺。",
                payload={
                    "contract_id": contract.contract_id,
                    "household_id": contract.household_id,
                    "signatory_name": contract.signatory_name,
                    "term_sheet": normalized,
                },
            )
        )
        text = str(result.data["contract_text"]).strip()
        if self._RESOURCE_AUTHORITY_CLAUSE not in text:
            text = f"{text}\n{self._RESOURCE_AUTHORITY_CLAUSE}"
        term_hash = self._hash(normalized)
        version = ContractVersion(
            version=len(contract.versions) + 1,
            text=text,
            term_hash=term_hash,
            text_hash=self._hash(text),
            created_by="contract_llm",
            warnings=tuple(str(item) for item in result.data.get("warnings", ())),
        )
        contract.term_sheet = normalized
        self._audit_contract_version(
            session, package, contract, version, normalized
        )
        contract.versions.append(version)
        contract.current_version = version.version
        contract.status = "draft"
        contract.review_decision = None
        contract.review_reason = ""
        contract.counteroffer = {}
        contract.updated_at = governance_now_iso()
        self._archive_contract_draft(session, contract)
        self._commit(session, state_version)
        return {
            "state_version": session.state_version,
            "contract": self._public_contract(contract, include_text=True),
        }

    def edit_contract(
        self,
        *,
        account_id: str,
        session_id: str,
        state_version: int,
        contract_id: str,
        text: str,
    ) -> dict:
        session, package = self._load_mutable(
            account_id, session_id, state_version
        )
        contract = self._contract(session, contract_id)
        if contract.status not in {
            "draft", "explanation_requested", "counteroffered", "rejected",
        } or contract.term_sheet is None:
            raise ActionUnavailableError("当前合同状态不能修改文本")
        content = text.strip()
        if not content:
            raise ActionUnavailableError("合同正文不能为空")
        version = ContractVersion(
            version=len(contract.versions) + 1,
            text=content,
            term_hash=self._hash(contract.term_sheet),
            text_hash=self._hash(content),
            created_by="player",
        )
        self._audit_contract_version(
            session, package, contract, version, contract.term_sheet
        )
        contract.versions.append(version)
        contract.current_version = version.version
        contract.status = "draft"
        contract.review_decision = None
        contract.review_reason = ""
        contract.counteroffer = {}
        contract.updated_at = governance_now_iso()
        self._archive_contract_draft(session, contract)
        self._commit(session, state_version)
        return {
            "state_version": session.state_version,
            "contract": self._public_contract(contract, include_text=True),
        }

    def submit_contract_review(
        self,
        *,
        account_id: str,
        session_id: str,
        state_version: int,
        contract_id: str,
    ) -> dict:
        session, package = self._load_mutable(
            account_id, session_id, state_version
        )
        contract = self._contract(session, contract_id)
        if contract.status != "draft" or contract.term_sheet is None:
            raise ActionUnavailableError("只有完成资源条款的草案可以送审")
        current_version = self._current_contract_version(contract)
        if current_version.audit_status != "pass":
            raise ActionUnavailableError(
                "合同专业审校尚未通过，不能送给签约人",
                details={
                    "audit_status": current_version.audit_status,
                    "audit": current_version.audit_result,
                },
            )
        self._validate_contract_text(
            contract,
            contract.term_sheet,
            self._current_contract_text(contract),
            package,
        )
        missing_conditions = self._missing_hard_conditions(
            session, package, contract
        )
        allowed = ["reject", "explain", "counteroffer"]
        if not missing_conditions:
            allowed.insert(0, "accept")
        actor_id, actor_name, actor_profile = self._contract_actor(
            package, contract
        )
        result = self._gateway.run_governance_task(
            GovernanceLLMContext(
                session_id=session.session_id,
                account_id=session.account_id,
                operation_id=(
                    f"{contract_id}:review:v{contract.current_version}:"
                    f"d{session.game_state.story_day}"
                ),
                story_day=session.game_state.story_day,
                task="review_contract",
                actor_id=actor_id,
                actor_name=actor_name,
                actor_profile=actor_profile,
                payload={
                    "contract_id": contract.contract_id,
                    "contract_text": self._current_contract_text(contract),
                    "term_sheet": contract.term_sheet,
                    "allowed_decisions": allowed,
                    "missing_hard_conditions": missing_conditions,
                    "contract_memory": list(contract.review_history[-8:]),
                },
            )
        )
        decision = str(result.data["decision"])
        status_by_decision = {
            "accept": "accepted",
            "reject": "rejected",
            "explain": "explanation_requested",
            "counteroffer": "counteroffered",
        }
        contract.status = status_by_decision[decision]
        contract.review_decision = decision
        contract.review_reason = str(result.data["reason"])
        contract.counteroffer = dict(result.data.get("counteroffer", {}))
        contract.review_history.append({
            "version": contract.current_version,
            "story_day": session.game_state.story_day,
            "decision": decision,
            "reason": contract.review_reason,
            "counteroffer": dict(contract.counteroffer),
        })
        contract.review_history = contract.review_history[-8:]
        if decision == "accept":
            self._reserve_contract_resources(session, package, contract)
            contract.reserved_until_day = min(
                89, session.game_state.story_day + 2
            )
        else:
            self._release_contract_reservations(
                session,
                contract.contract_id,
                reason=f"review_{decision}",
            )
            contract.reserved_until_day = None
        contract.updated_at = governance_now_iso()
        self._commit(session, state_version)
        return {
            "state_version": session.state_version,
            "missing_hard_conditions": missing_conditions,
            "contract": self._public_contract(contract, include_text=True),
        }

    def sign_contract(
        self,
        *,
        account_id: str,
        session_id: str,
        state_version: int,
        contract_id: str,
        confirmed: bool,
    ) -> dict:
        session, package = self._load_mutable(
            account_id, session_id, state_version
        )
        contract = self._contract(session, contract_id)
        if not confirmed:
            return {
                "state_version": session.state_version,
                "signed": False,
                "contract": self._public_contract(contract),
            }
        if session.game_state.story_day >= 90:
            raise ActionUnavailableError("D90只验收，不再新增签约")
        if contract.status != "accepted" or contract.term_sheet is None:
            raise ActionUnavailableError("合同尚未被本户签约人接受")
        if (
            contract.reserved_until_day is not None
            and session.game_state.story_day > contract.reserved_until_day
        ):
            self._release_contract_reservations(
                session, contract_id, reason="expired_before_signing"
            )
            contract.status = "draft"
            raise ActionUnavailableError("资源预占已经过期，请重新送审")
        if any(
            item.household_id == contract.household_id
            and item.status == "signed"
            for item in session.household_contracts.values()
            if item.contract_id != contract.contract_id
        ):
            raise ActionUnavailableError("该家庭已经存在有效搬迁主合同")
        if session.game_state.signed_households >= session.game_state.total_households:
            raise ActionUnavailableError("真实签约户数已经达到36户")
        self._validate_contract_reservations(session, contract)
        signed_hash = self._hash({
            "contract_id": contract.contract_id,
            "version": contract.current_version,
            "term_sheet": contract.term_sheet,
            "text": self._current_contract_text(contract),
        })
        for reservation in session.resource_reservations:
            if (
                reservation.owner_type == "contract"
                and reservation.owner_id == contract_id
                and reservation.status == "reserved"
            ):
                reservation.status = "committed"
                reservation.committed_day = session.game_state.story_day
                reservation.expires_day = None
                self._record_resource_event(
                    session,
                    change_kind="commitment",
                    source_type="signed_contract",
                    source_id=contract.contract_id,
                    resource_id=reservation.resource_id,
                    quantity=reservation.quantity,
                    reservation_id=reservation.reservation_id,
                    payment_status="unpaid",
                )
        cash = int(contract.term_sheet["cash_amount"])
        state = session.game_state
        session.game_state = replace(
            state,
            budget_committed=state.budget_committed + cash,
            signed_households=state.signed_households + 1,
            reported_signed_households=max(
                state.reported_signed_households,
                state.signed_households + 1,
            ),
        )
        self._record_resource_event(
            session,
            change_kind="ledger_commitment",
            source_type="signed_contract",
            source_id=contract.contract_id,
            resource_id="budget_committed",
            quantity=cash,
            delta=cash,
            before=state.budget_committed,
            after=state.budget_committed + cash,
            payment_status="unpaid",
        )
        contract.status = "signed"
        contract.signed_day = session.game_state.story_day
        contract.signed_hash = signed_hash
        contract.updated_at = governance_now_iso()
        entry_batch = (
            "first_batch"
            if session.game_state.story_day <= 75
            else "post75_confirmation"
        )
        session.household_settlement_entries.append(
            HouseholdSettlementEntry(
                entry_id=f"settlement_{secrets.token_hex(10)}",
                household_group_id=contract.household_id,
                household_count=1,
                signed_day=session.game_state.story_day,
                entry_batch=entry_batch,
                entry_type="individual_contract",
                source_node_id=contract.contract_id,
                policy_version=str(contract.term_sheet["policy_document_id"]),
                eligibility_registered_day=contract.created_day,
                early_reward_paid=bool(
                    contract.term_sheet.get("public_window_reward", False)
                ),
                contract_id=contract.contract_id,
                household_id=contract.household_id,
                resource_details=dict(contract.term_sheet),
            )
        )
        archive_id = f"archive:contract:{contract.contract_id}:signed"
        contract.archive_id = archive_id
        session.archive_records[archive_id] = ArchiveRecord(
            archive_id=archive_id,
            category="逐户合同",
            title=f"{contract.household_id}搬迁补偿安置合同（已签署）",
            content=self._current_contract_text(contract),
            source_type="household_contract",
            source_id=contract.contract_id,
            acquired_day=session.game_state.story_day,
            acquired_via="contract_signing",
            evidence_level="E3",
            confidentiality="private",
        )
        self._commit(session, state_version)
        return {
            "state_version": session.state_version,
            "signed": True,
            "contract": self._public_contract(contract, include_text=True),
            "visible_state": self._projector.project(session, package),
        }

    def settle_due_contracts(
        self, session: GameSession, package: ScriptPackage
    ) -> list[dict]:
        """日终推进后调用；只按已签合同的结构化期限结算。"""

        day = session.game_state.story_day
        results = []
        for contract in session.household_contracts.values():
            if contract.status != "signed" or contract.term_sheet is None:
                continue
            terms = contract.term_sheet
            changed = {}
            if (
                not contract.fulfillment.get("cash_paid")
                and int(terms["payment_day"]) <= day
            ):
                cash = int(terms["cash_amount"])
                if session.game_state.budget_remaining < cash:
                    contract.fulfillment["payment_default"] = True
                    changed["payment_default"] = True
                    self._record_resource_event(
                        session,
                        change_kind="payment_default",
                        source_type="contract_fulfillment",
                        source_id=contract.contract_id,
                        resource_id="budget_remaining",
                        quantity=cash,
                        fulfillment_node=f"payment_day:D{day}",
                        payment_status="defaulted",
                    )
                else:
                    state = session.game_state
                    session.game_state = replace(
                        state,
                        budget_remaining=state.budget_remaining - cash,
                        budget_paid=state.budget_paid + cash,
                    )
                    contract.fulfillment["cash_paid"] = True
                    changed["cash_paid"] = cash
                    for reservation in session.resource_reservations:
                        if (
                            reservation.owner_type == "contract"
                            and reservation.owner_id == contract.contract_id
                            and reservation.status == "committed"
                            and reservation.resource_id.startswith("budget:")
                        ):
                            reservation.status = "delivered"
                            reservation.delivered_day = day
                    self._record_resource_event(
                        session,
                        change_kind="payment",
                        source_type="contract_fulfillment",
                        source_id=contract.contract_id,
                        resource_id="budget_remaining",
                        quantity=cash,
                        delta=-cash,
                        before=state.budget_remaining,
                        after=state.budget_remaining - cash,
                        fulfillment_node=f"payment_day:D{day}",
                        payment_status="paid",
                    )
            if (
                not contract.fulfillment.get("resources_delivered")
                and int(terms["housing_delivery_day"]) <= day
            ):
                for reservation in session.resource_reservations:
                    if (
                        reservation.owner_type == "contract"
                        and reservation.owner_id == contract.contract_id
                        and reservation.status == "committed"
                        and not reservation.resource_id.startswith("budget:")
                    ):
                        reservation.status = "delivered"
                        reservation.delivered_day = day
                        self._record_resource_event(
                            session,
                            change_kind="delivery",
                            source_type="contract_fulfillment",
                            source_id=contract.contract_id,
                            resource_id=reservation.resource_id,
                            quantity=reservation.quantity,
                            reservation_id=reservation.reservation_id,
                            fulfillment_node=f"housing_delivery_day:D{day}",
                            payment_status="not_applicable",
                        )
                contract.fulfillment["resources_delivered"] = True
                changed["resources_delivered"] = True
            if changed:
                contract.updated_at = governance_now_iso()
                results.append({
                    "contract_id": contract.contract_id,
                    "household_id": contract.household_id,
                    **changed,
                })
        return results

    def expire_reservations(self, session: GameSession) -> list[str]:
        day = session.game_state.story_day
        expired = []
        for reservation in session.resource_reservations:
            if (
                reservation.status == "reserved"
                and reservation.expires_day is not None
                and reservation.expires_day < day
            ):
                reservation.status = "released"
                source_type = (
                    "contract_review"
                    if reservation.owner_type == "contract"
                    else "player_choice"
                )
                self._record_resource_event(
                    session,
                    change_kind="release",
                    source_type=source_type,
                    source_id=reservation.owner_id,
                    resource_id=reservation.resource_id,
                    quantity=reservation.quantity,
                    reservation_id=reservation.reservation_id,
                    release_reason="reservation_expired",
                    payment_status="unpaid",
                )
                expired.append(reservation.owner_id)
        for contract_id in set(expired):
            contract = session.household_contracts.get(contract_id)
            if contract is not None and contract.status != "signed":
                contract.status = "draft"
                contract.reserved_until_day = None
        return sorted(set(expired))

    def _validate_action_targets(
        self,
        package: ScriptPackage,
        *,
        action_kind: str,
        target_ids: tuple[str, ...],
        archive_ids: tuple[str, ...],
        topic: str,
        proposed_document_type: str | None,
        lead_npc_id: str | None,
        session: GameSession,
    ) -> None:
        config = package.governance_config or {}
        visible_npc_ids = self._visible_governance_npc_ids(session, package)
        if action_kind == "household_visit":
            allowed = (
                set(config.get("household_representative_npc_ids", ()))
                & visible_npc_ids
            )
            if len(target_ids) != 1 or target_ids[0] not in allowed:
                raise ActionUnavailableError("入户走访必须选择一名家庭代表")
        elif action_kind == "cadre_interview":
            allowed = set(config.get("cadre_npc_ids", ())) & visible_npc_ids
            if not 1 <= len(target_ids) <= 3 or not set(target_ids).issubset(allowed):
                raise ActionUnavailableError("干部访谈必须选择1至3名已登记干部")
        elif action_kind == "leadership_meeting":
            eligible = set(config.get("leadership_meeting_npc_ids", ()))
            eligible &= visible_npc_ids
            if not 2 <= len(target_ids) <= 8:
                raise ActionUnavailableError("班子会议必须有2至8名领导干部参会")
            if len(set(target_ids)) != len(target_ids) or not set(
                target_ids
            ).issubset(eligible):
                raise ActionUnavailableError("班子会议只能邀请已公开的领导干部")
            if not lead_npc_id or lead_npc_id not in target_ids:
                raise ActionUnavailableError("必须从参会领导中指定一名分管或牵头领导")
            if not topic.strip():
                raise ActionUnavailableError("班子会议必须填写具体议题")
            if proposed_document_type:
                rules = config.get("document_rules", {})
                if proposed_document_type not in rules:
                    raise ActionUnavailableError("拟形成的文件类型未登记")
                required = set(
                    rules[proposed_document_type].get(
                        "required_countersign_ids", ()
                    )
                )
                if not required.issubset(target_ids):
                    raise ActionUnavailableError(
                        "必要会签人必须参加形成文件的会议",
                        details={"missing_participants": sorted(
                            required - set(target_ids)
                        )},
                    )
                missing_archives = [
                    archive_id for archive_id in archive_ids
                    if archive_id not in session.archive_records
                    or session.archive_records[archive_id].status != "available"
                ]
                if missing_archives:
                    raise ActionUnavailableError(
                        "会议引用了尚未取得的档案",
                        details={"archive_ids": sorted(missing_archives)},
                    )
                required_level = str(
                    rules[proposed_document_type].get(
                        "required_evidence_level", "E0"
                    )
                )
                evidence_rank = {"E0": 0, "E1": 1, "E2": 2, "E3": 3}
                highest = max(
                    (
                        evidence_rank.get(
                            session.archive_records[archive_id].evidence_level,
                            0,
                        )
                        for archive_id in archive_ids
                    ),
                    default=0,
                )
                if highest < evidence_rank[required_level]:
                    raise ActionUnavailableError(
                        "现有材料的证据等级不足以形成该类红头文件",
                        details={
                            "required_evidence_level": required_level,
                            "highest_evidence_level": f"E{highest}",
                        },
                    )
        elif action_kind == "inspect_archives":
            sync_known_facts_to_archives(session, package)
            if not archive_ids:
                raise ActionUnavailableError("查阅档案必须选择具体档案")
            missing = sorted(
                archive_id for archive_id in archive_ids
                if archive_id not in session.archive_records
                or session.archive_records[archive_id].status != "available"
            )
            if missing:
                raise ActionUnavailableError(
                    "所选档案尚未通过剧情或行为取得",
                    details={"archive_ids": missing},
                )

    @staticmethod
    def _visible_governance_npc_ids(
        session: GameSession,
        package: ScriptPackage,
    ) -> set[str]:
        """Return NPCs whose identities have been introduced to the player.

        Visibility deliberately ignores an opportunity's day_max and completion
        state: once a person has entered the story, their public identity remains
        known. The opening whitelist covers officials and village representatives
        whose identities are public before the first scripted encounter.
        """
        config = package.governance_config or {}
        visible = set(config.get("initial_visible_npc_ids", ()))
        day = session.game_state.story_day
        for opportunity in package.interaction_opportunities:
            if opportunity.availability_mode is AvailabilityMode.CLOSED:
                continue
            if day < opportunity.day_min:
                continue
            if not opportunity.requires_flags.issubset(session.flags):
                continue
            if not opportunity.requires_events.issubset(session.triggered_events):
                continue
            visible.add(opportunity.npc_id)
        return visible

    def _detect_and_create_contract_batch(
        self,
        session: GameSession,
        package: ScriptPackage,
        representative_npc_id: str,
        player_text: str,
    ) -> ContractBatch | None:
        existing = next(
            (
                item for item in session.contract_batches.values()
                if item.representative_npc_id == representative_npc_id
                and item.status == "pending_confirmation"
            ),
            None,
        )
        if existing is not None:
            return existing
        gate_flag = str(
            (package.governance_config or {})
            .get("contract_batch_gate_flags", {})
            .get(representative_npc_id, "")
        )
        if gate_flag and gate_flag not in session.flags:
            return None
        profile = next(
            item for item in package.npc_profiles
            if item.npc_id == representative_npc_id
        )
        result = self._gateway.run_governance_task(
            GovernanceLLMContext(
                session_id=session.session_id,
                account_id=session.account_id,
                operation_id=(
                    f"contract-intent:{session.session_id}:"
                    f"{representative_npc_id}:{len(session.contract_batches) + 1}"
                ),
                story_day=session.game_state.story_day,
                task="detect_contract_intent",
                actor_id=representative_npc_id,
                actor_name=profile.name,
                actor_profile=profile.role_setting,
                payload={"player_text": player_text},
            )
        )
        if result.data["intent"] != "request_contract_batch":
            return None
        households = package.contract_batch_for_representative(
            representative_npc_id
        )
        batch = ContractBatch(
            batch_id=f"batch_{secrets.token_hex(10)}",
            representative_npc_id=representative_npc_id,
            story_day=session.game_state.story_day,
            household_ids=tuple(item.household_id for item in households),
            status="pending_confirmation",
            player_request=player_text,
            intent_reason=str(result.data["reason"]),
        )
        session.contract_batches[batch.batch_id] = batch
        return batch

    def _acquire_archives_from_interaction(
        self,
        session: GameSession,
        package: ScriptPackage,
        action: GovernanceActionRecord,
        player_text: str,
    ) -> list[str]:
        text = player_text.replace(" ", "")
        acquired = []
        rules = (
            (
                {"npc_feng_jingzhi"},
                ("200万", "专账", "付款路径"),
                "archive_finance_2m_ledger",
                "财政档案",
                "200万元前期协调费专账复制件",
                "预算来源、前期协调科目与付款节点的财政复制件。",
                "E2",
            ),
            (
                {"npc_ke_qinian"},
                ("环评批复", "验收依据"),
                "archive_formal_eia_approval",
                "环保档案",
                "宏达项目正式环评批复副本",
                "县级正式环评批复与登记的验收依据。",
                "E2",
            ),
            (
                {"npc_zhou_kuiyuan"},
                ("1983", "迁坟先例", "迁坟年代"),
                "archive_1983_grave_index",
                "档案索引",
                "1983年迁坟先例检索索引",
                "取得历史迁坟的年代与经办线索，可据此进一步调阅历史批复。",
                "E1",
            ),
            (
                {"npc_tan_laoliu"},
                ("旧案年份", "项目名", "拆违"),
                "archive_tan_case_index",
                "信访索引",
                "谭老六历史旧案检索索引",
                "取得旧案年份、项目名称和后续调卷所需索引。",
                "E1",
            ),
        )
        targets = set(action.target_ids)
        for (
            allowed_targets, keywords, archive_id, category,
            title, content, evidence,
        ) in rules:
            if (
                targets & allowed_targets
                and any(keyword in text for keyword in keywords)
                and archive_id not in session.archive_records
            ):
                session.archive_records[archive_id] = ArchiveRecord(
                    archive_id=archive_id,
                    category=category,
                    title=title,
                    content=content,
                    source_type="interaction",
                    source_id=action.action_instance_id,
                    acquired_day=session.game_state.story_day,
                    acquired_via=action.action_kind,
                    evidence_level=evidence,
                    confidentiality="internal",
                    related_npc_ids=tuple(targets & allowed_targets),
                )
                action.result_ids.append(archive_id)
                acquired.append(archive_id)
        return acquired

    def _validate_resolution(
        self,
        session: GameSession,
        package: ScriptPackage,
        meeting: MeetingRecord,
        value: dict,
    ) -> dict:
        required = {
            "decision",
            "target_scope",
            "resources",
            "responsible_ids",
            "deadline_day",
            "public_scope",
            "document_title",
        }
        optional = {"resource_mode"}
        if not required.issubset(value) or set(value) - required - optional:
            raise ActionUnavailableError(
                "会议决议字段不完整",
                details={
                    "missing": sorted(required - set(value)),
                    "unexpected": sorted(set(value) - required - optional),
                },
            )
        deadline = int(value["deadline_day"])
        if not session.game_state.story_day <= deadline <= 90:
            raise ActionUnavailableError("会议决议期限必须在当前日至D90之间")
        responsible = tuple(dict.fromkeys(
            str(item) for item in value["responsible_ids"]
        ))
        profile_ids = {item.npc_id for item in package.npc_profiles}
        if not responsible or not set(responsible).issubset(profile_ids):
            raise ActionUnavailableError("会议决议责任人必须是已登记NPC")
        resources = {
            str(key): int(amount)
            for key, amount in dict(value["resources"]).items()
            if int(amount) > 0
        }
        pools = {
            item["resource_id"]: item
            for item in (package.governance_config or {}).get(
                "resource_pools", []
            )
        }
        capacities = {
            str(resource_id): int(item["capacity"])
            for resource_id, item in pools.items()
        }
        capacities.update({
            f"budget:{key}": int(amount)
            for key, amount in (package.governance_config or {}).get(
                "budget_envelopes", {}
            ).items()
        })
        unknown = sorted(set(resources) - set(capacities))
        if unknown:
            raise ActionUnavailableError(
                "会议决议引用未知资源",
                details={"resource_ids": unknown},
            )
        for resource_id, amount in resources.items():
            if amount > capacities[resource_id]:
                raise ActionUnavailableError(
                    "会议决议资源数量超过全局容量",
                    details={"resource_id": resource_id},
                )
        resource_mode = str(
            value.get("resource_mode", "authorization_ceiling")
        )
        if resource_mode != "authorization_ceiling":
            raise ActionUnavailableError(
                "红头文件只能形成资源授权上限；"
                "具体资源由逐户合同或执行凭证占用"
            )
        return {
            "decision": str(value["decision"]).strip(),
            "target_scope": str(value["target_scope"]).strip(),
            "resource_mode": resource_mode,
            "resource_authorization_limits": resources,
            "responsible_ids": list(responsible),
            "deadline_day": deadline,
            "public_scope": [
                str(item).strip()
                for item in value["public_scope"]
                if str(item).strip()
            ],
            "document_title": str(value["document_title"]).strip(),
            "evidence_archive_ids": list(
                session.governance_actions[
                    meeting.action_instance_id
                ].archive_ids
            ),
        }

    def _meeting_passed(
        self,
        meeting: MeetingRecord,
        *,
        adopt: bool,
        positions: dict[str, dict],
    ) -> tuple[bool, str]:
        if not adopt:
            return False, "玩家没有采纳议案"
        if meeting.decision_mode == "executive_decision":
            return True, ""
        if meeting.decision_mode == "dual_key":
            jiang = positions.get("npc_jiang_chongyue")
            if jiang is None or jiang["position"] not in {
                "approve", "conditional",
            }:
                return False, "重大事项未取得县委书记共同批准"
            return True, ""
        if meeting.decision_mode == "formal_vote":
            eligible = [
                item for item in positions.values()
                if item["position"] != "abstain"
            ]
            quorum = math.ceil(len(positions) * 2 / 3)
            if len(eligible) < quorum:
                return False, "实到表决人数不足三分之二"
            approvals = sum(
                item["position"] in {"approve", "conditional"}
                for item in eligible
            )
            if approvals <= len(eligible) / 2:
                return False, "赞成票未超过实到表决成员半数"
            return True, ""
        return False, "未知会议决策机制"

    def _draft_document(
        self,
        session: GameSession,
        package: ScriptPackage,
        meeting: MeetingRecord,
    ) -> AdministrativeDocument:
        assert meeting.resolution is not None
        document_type = str(meeting.proposed_document_type)
        rules = (package.governance_config or {})["document_rules"][
            document_type
        ]
        title = str(meeting.resolution["document_title"])
        document_id = f"doc_{secrets.token_hex(10)}"
        result = self._gateway.run_governance_task(
            GovernanceLLMContext(
                session_id=session.session_id,
                account_id=session.account_id,
                operation_id=f"{meeting.meeting_id}:draft-document",
                story_day=session.game_state.story_day,
                task="draft_document",
                actor_id="document_writer",
                actor_name="行政文书模型",
                actor_profile="只转写已经通过的会议决议。",
                payload={
                    "meeting_id": meeting.meeting_id,
                    "document_type": document_type,
                    "title": title,
                    "resolution": meeting.resolution,
                },
            )
        )
        content = str(result.data["document_text"]).strip()
        if self._RESOURCE_AUTHORITY_CLAUSE not in content:
            content = f"{content}\n{self._RESOURCE_AUTHORITY_CLAUSE}"
        document = AdministrativeDocument(
            document_id=document_id,
            document_type=document_type,
            title=title,
            status="draft",
            version=1,
            content=content,
            story_day=session.game_state.story_day,
            policy_version=f"meeting-{meeting.meeting_id}-v1",
            source_meeting_id=meeting.meeting_id,
            resolution_snapshot=dict(meeting.resolution),
            required_countersign_ids=tuple(
                rules.get("required_countersign_ids", ())
            ),
            public_scope=tuple(meeting.resolution.get("public_scope", ())),
        )
        self._record_document_version(
            document,
            created_by="document_writer",
            model_id=result.model_id,
            change_summary="根据已通过的会议决议形成行政文件初稿。",
        )
        self._review_and_revise_document(
            session,
            package,
            document,
            review_stage="draft_review",
        )
        session.administrative_documents[document_id] = document
        return document

    @classmethod
    def _structured_document_content(
        cls,
        *,
        title: str,
        resolution: dict,
    ) -> str:
        """Render a safe administrative draft from validated fields only."""
        resources = (
            resolution.get("resource_allocations")
            or resolution.get("resource_authorization_limits")
            or {}
        )
        lines = [
            title,
            f"决议事项：{resolution.get('decision', '')}",
            f"适用范围：{resolution.get('target_scope', '')}",
            "责任主体：" + "、".join(
                str(item) for item in resolution.get("responsible_ids", ())
            ),
            f"办理期限：D{resolution.get('deadline_day', '')}",
            "公开范围：" + "、".join(
                str(item) for item in resolution.get("public_scope", ())
            ),
        ]
        if resources:
            lines.append("资源授权上限：" + "；".join(
                f"{resource_id}={amount}"
                for resource_id, amount in sorted(resources.items())
            ))
        lines.append(cls._RESOURCE_AUTHORITY_CLAUSE)
        return "\n".join(item for item in lines if item.strip())

    def _review_and_revise_document(
        self,
        session: GameSession,
        package: ScriptPackage,
        document: AdministrativeDocument,
        *,
        review_stage: str,
    ) -> None:
        if not document.version_history:
            self._record_document_version(
                document,
                created_by="legacy_document",
                model_id="legacy-import",
                change_summary="纳入行政文书审校链的既有文本。",
            )
        review = self._audit_document(
            session, package, document, stage=review_stage
        )
        if review["status"] == "pass":
            return
        revision = self._gateway.run_governance_task(
            GovernanceLLMContext(
                session_id=session.session_id,
                account_id=session.account_id,
                operation_id=(
                    f"{document.document_id}:revise:v{document.version}:"
                    f"review-{len(document.review_history)}"
                ),
                story_day=session.game_state.story_day,
                task="revise_document",
                actor_id="document_reviser",
                actor_name="行政文书自动修订模型",
                actor_profile=(
                    "只处理独立审校已经指出的问题，不改变会议决议。"
                ),
                prompt_version="administrative-document-revision-v1",
                payload={
                    "document_id": document.document_id,
                    "document_type": document.document_type,
                    "title": document.title,
                    "document_text": document.content,
                    "resolution": document.resolution_snapshot,
                    "review": review,
                    "safe_reference_text": self._structured_document_content(
                        title=document.title,
                        resolution=document.resolution_snapshot,
                    ),
                },
            )
        )
        revised_text = str(revision.data["document_text"]).strip()
        if self._RESOURCE_AUTHORITY_CLAUSE not in revised_text:
            revised_text = (
                f"{revised_text}\n{self._RESOURCE_AUTHORITY_CLAUSE}"
            )
        previous_version = document.version
        document.version += 1
        document.content = revised_text
        document.updated_at = governance_now_iso()
        document.revision_history.append({
            "from_version": previous_version,
            "to_version": document.version,
            "model_id": revision.model_id,
            "change_summary": str(revision.data["change_summary"]),
            "addressed_issue_ids": list(
                revision.data.get("addressed_issue_ids", ())
            ),
            "revised_at": governance_now_iso(),
        })
        self._record_document_version(
            document,
            created_by="document_revision_agent",
            model_id=revision.model_id,
            change_summary=str(revision.data["change_summary"]),
        )
        post_revision = self._audit_document(
            session, package, document, stage="post_revision_review"
        )
        if post_revision["status"] == "pass":
            return
        fallback = self._structured_document_content(
            title=document.title,
            resolution=document.resolution_snapshot,
        )
        if fallback != document.content:
            previous_version = document.version
            document.version += 1
            document.content = fallback
            document.updated_at = governance_now_iso()
            document.revision_history.append({
                "from_version": previous_version,
                "to_version": document.version,
                "model_id": "deterministic-safety-renderer-v1",
                "change_summary": (
                    "自动修订稿仍未通过审校，已回到会议决议安全文本。"
                ),
                "addressed_issue_ids": [
                    str(item.get("issue_id"))
                    for item in post_revision.get("issues", ())
                ],
                "revised_at": governance_now_iso(),
            })
            self._record_document_version(
                document,
                created_by="deterministic_safety_renderer",
                model_id="deterministic-safety-renderer-v1",
                change_summary="根据会议决议生成最终安全文本。",
            )
        self._audit_document(
            session, package, document, stage="safety_fallback_review"
        )

    def _audit_document(
        self,
        session: GameSession,
        package: ScriptPackage,
        document: AdministrativeDocument,
        *,
        stage: str,
    ) -> dict:
        result = self._gateway.run_governance_task(
            GovernanceLLMContext(
                session_id=session.session_id,
                account_id=session.account_id,
                operation_id=(
                    f"{document.document_id}:audit:v{document.version}:"
                    f"{self._hash(document.content)}"
                ),
                story_day=session.game_state.story_day,
                task="audit_document",
                actor_id="document_reviewer",
                actor_name="行政文书独立审校模型",
                actor_profile=(
                    "独立审校行政文件，只定位问题，不替代修订模型。"
                ),
                prompt_version="administrative-document-audit-v1",
                payload={
                    "document_id": document.document_id,
                    "document_type": document.document_type,
                    "title": document.title,
                    "document_text": document.content,
                    "resolution": document.resolution_snapshot,
                    "resource_authority_clause": (
                        self._RESOURCE_AUTHORITY_CLAUSE
                    ),
                },
            )
        )
        issues = [dict(item) for item in result.data.get("issues", ())]
        try:
            self._validate_document_consistency(
                document, document.content, package
            )
        except ActionUnavailableError as exc:
            issues.append({
                "issue_id": "DOC-AUDIT-DETERMINISTIC-001",
                "severity": "error",
                "category": "resolution_consistency",
                "message": exc.message,
                "text_quote": document.content[:120] or "（正文为空）",
                "suggestion": (
                    "严格按会议决议、结构化资源上限和权限条款修订。"
                ),
                "details": exc.details,
            })
        status = str(result.data["status"])
        summary = str(result.data["summary"])
        if issues and status == "pass":
            status = "needs_revision"
            summary = "文书存在必须修订的一致性或权限问题。"
        reviewed_at = governance_now_iso()
        review = {
            "version": document.version,
            "stage": stage,
            "status": status,
            "summary": summary,
            "issues": issues,
            "model_id": result.model_id,
            "reviewed_at": reviewed_at,
        }
        document.review_status = status
        document.review_summary = summary
        document.review_model_id = result.model_id
        document.reviewed_at = reviewed_at
        document.review_history.append(review)
        return review

    def _record_document_version(
        self,
        document: AdministrativeDocument,
        *,
        created_by: str,
        model_id: str,
        change_summary: str,
    ) -> None:
        document.version_history.append({
            "version": document.version,
            "content": document.content,
            "content_hash": self._hash(document.content),
            "created_by": created_by,
            "model_id": model_id,
            "change_summary": change_summary,
            "created_at": governance_now_iso(),
        })

    def _validate_document_consistency(
        self,
        document: AdministrativeDocument,
        content: str,
        package: ScriptPackage,
    ) -> None:
        resolution = document.resolution_snapshot
        if not resolution:
            return
        required_tokens = (
            str(resolution.get("decision", "")),
            str(resolution.get("target_scope", "")),
            str(resolution.get("deadline_day", "")),
        )
        missing = [item for item in required_tokens if item and item not in content]
        resources = (
            resolution.get("resource_allocations")
            or resolution.get("resource_authorization_limits")
            or {}
        )
        for resource_id, amount in resources.items():
            if resource_id not in content or str(amount) not in content:
                missing.append(f"{resource_id}:{amount}")
        if self._RESOURCE_AUTHORITY_CLAUSE not in content:
            missing.append("结构化资源权威条款")
        self._validate_no_unstructured_commitments(
            content,
            structured=resolution,
            expected_resource_ids=set(resources),
            package=package,
        )
        if missing:
            raise ActionUnavailableError(
                "文件正文与会议决议不一致",
                details={"missing_resolution_values": missing},
            )

    def _validate_no_unstructured_commitments(
        self,
        content: str,
        *,
        structured: dict,
        expected_resource_ids: set[str],
        package: ScriptPackage,
    ) -> None:
        details = self._unstructured_commitment_details(
            content,
            structured=structured,
            expected_resource_ids=expected_resource_ids,
            package=package,
        )
        if details["resource_ids"] or details["numbers"]:
            raise ActionUnavailableError(
                "正文包含结构化附件之外的资源或金额承诺",
                details=details,
            )

    def _unstructured_commitment_details(
        self,
        content: str,
        *,
        structured: dict,
        expected_resource_ids: set[str],
        package: ScriptPackage,
    ) -> dict[str, list[str]]:
        config = package.governance_config or {}
        known_resource_ids = {
            str(item["resource_id"])
            for item in config.get("resource_pools", [])
        }
        known_resource_ids.update(
            f"budget:{key}"
            for key in config.get("budget_envelopes", {})
        )
        extra_resource_ids = sorted(
            resource_id
            for resource_id in known_resource_ids - expected_resource_ids
            if re.search(
                rf"(?<![A-Za-z0-9_.:-]){re.escape(resource_id)}"
                rf"(?![A-Za-z0-9_.:-])",
                content,
            )
        )
        structured_text = json.dumps(
            structured, ensure_ascii=False, sort_keys=True
        )
        allowed_numbers = set(
            self._STRUCTURED_NUMBER_PATTERN.findall(structured_text)
        )
        extra_numbers = sorted(
            set(self._STRUCTURED_NUMBER_PATTERN.findall(content))
            - allowed_numbers
        )
        return {
            "resource_ids": extra_resource_ids,
            "numbers": extra_numbers,
        }

    def _validate_term_sheet(
        self,
        session: GameSession,
        package: ScriptPackage,
        contract: HouseholdContract,
        value: dict,
    ) -> dict:
        required = {
            "policy_document_id",
            "cash_amount",
            "budget_envelope",
            "housing_resource_id",
            "service_allocations",
            "payment_day",
            "move_out_day",
            "housing_delivery_day",
            "transition_months",
            "public_window_reward",
            "approval_document_ids",
            "authorization_confirmed",
            "real_unit_viewed",
            "ledger_disclosed",
            "old_case_resolved",
            "prior_payment_verified",
        }
        if set(value) != required:
            raise ActionUnavailableError(
                "合同资源条款字段不完整",
                details={
                    "missing": sorted(required - set(value)),
                    "unexpected": sorted(set(value) - required),
                },
            )
        policy_id = str(value["policy_document_id"])
        policy = session.administrative_documents.get(policy_id)
        if (
            policy is None
            or policy.document_type != "compensation_policy"
            or policy.status not in {"issued", "published"}
        ):
            raise ActionUnavailableError("合同必须引用有效补偿方案")
        household = self._household(package, contract.household_id)
        cash = int(value["cash_amount"])
        months = int(value["transition_months"])
        if cash < 0 or not 0 <= months <= 12:
            raise ActionUnavailableError("合同现金或过渡月数无效")
        minimum = self._standard_cash(
            package, household, months=months,
            reward=bool(value["public_window_reward"]),
        )
        if cash < minimum:
            raise ActionUnavailableError(
                "合同现金低于开局政策标准",
                details={"minimum": minimum, "submitted": cash},
            )
        config = package.governance_config or {}
        envelope = str(value["budget_envelope"])
        if envelope not in config.get("budget_envelopes", {}):
            raise ActionUnavailableError("合同引用未知预算信封")
        approval_ids = tuple(str(item) for item in value["approval_document_ids"])
        invalid_approval_ids = [
            document_id
            for document_id in approval_ids
            if (
                document_id not in session.administrative_documents
                or session.administrative_documents[document_id].status
                not in {"issued", "published"}
            )
        ]
        if invalid_approval_ids:
            raise ActionUnavailableError(
                "合同引用了无效或尚未签发的批准文件",
                details={"document_ids": invalid_approval_ids},
            )
        if cash - minimum > 20 and not any(
            (
                document_id in session.administrative_documents
                and session.administrative_documents[
                    document_id
                ].document_type == "compensation_adjustment"
                and session.administrative_documents[
                    document_id
                ].status in {"issued", "published"}
            )
            for document_id in approval_ids
        ):
            raise ActionUnavailableError(
                "单户标准外增加超过20万元必须引用已签发补偿调整文件"
            )
        payment_day = int(value["payment_day"])
        move_out_day = int(value["move_out_day"])
        delivery_day = int(value["housing_delivery_day"])
        if not (
            session.game_state.story_day <= payment_day <= 90
            and session.game_state.story_day <= move_out_day <= 90
            and session.game_state.story_day <= delivery_day <= 90
        ):
            raise ActionUnavailableError("合同履行日期必须在当前日至D90之间")
        pools = {
            str(item["resource_id"]): item
            for item in config.get("resource_pools", [])
        }
        housing_id = (
            str(value["housing_resource_id"])
            if value["housing_resource_id"] else None
        )
        if housing_id is not None:
            housing = pools.get(housing_id)
            if housing is None or housing.get("category") != "housing":
                raise ActionUnavailableError("合同引用未知安置房资源")
            if int(housing["available_day"]) > delivery_day:
                raise ActionUnavailableError("合同交房日早于房源可交付日")
            required_area = {2: 80, 3: 100, 4: 120, 5: 140}.get(
                household.resettlement_population, 140
            )
            if int(housing["attributes"]["area_m2"]) < required_area:
                raise ActionUnavailableError("安置房面积低于本户安置人口档位")
            if (
                "low_floor" in household.resettlement_preference
                and not bool(housing["attributes"].get("accessible"))
            ):
                raise ActionUnavailableError("本户需要低楼层无障碍房源")
        elif (
            household.resettlement_preference.startswith("resettlement_house")
            or "low_floor" in household.resettlement_preference
        ):
            raise ActionUnavailableError("本户选择实物安置时必须指定房源")
        allocations = {
            str(resource_id): int(amount)
            for resource_id, amount in dict(
                value["service_allocations"]
            ).items()
            if int(amount) > 0
        }
        unknown = sorted(
            resource_id for resource_id in allocations
            if resource_id not in pools
            or pools[resource_id].get("category") == "housing"
        )
        if unknown:
            raise ActionUnavailableError(
                "合同引用未知服务资源",
                details={"resource_ids": unknown},
            )
        return {
            "policy_document_id": policy_id,
            "cash_amount": cash,
            "policy_minimum_cash": minimum,
            "budget_envelope": envelope,
            "housing_resource_id": housing_id,
            "service_allocations": allocations,
            "payment_day": payment_day,
            "move_out_day": move_out_day,
            "housing_delivery_day": delivery_day,
            "transition_months": months,
            "public_window_reward": bool(value["public_window_reward"]),
            "approval_document_ids": list(approval_ids),
            "authorization_confirmed": bool(value["authorization_confirmed"]),
            "real_unit_viewed": bool(value["real_unit_viewed"]),
            "ledger_disclosed": bool(value["ledger_disclosed"]),
            "old_case_resolved": bool(value["old_case_resolved"]),
            "prior_payment_verified": bool(value["prior_payment_verified"]),
        }

    def _reserve_contract_resources(
        self,
        session: GameSession,
        package: ScriptPackage,
        contract: HouseholdContract,
    ) -> None:
        assert contract.term_sheet is not None
        self._release_contract_reservations(
            session, contract.contract_id, reason="review_replaced"
        )
        terms = contract.term_sheet
        requested = self._contract_resource_request(contract)
        config = package.governance_config or {}
        capacities = {
            f"budget:{key}": int(value)
            for key, value in config.get("budget_envelopes", {}).items()
        }
        capacities.update({
            str(item["resource_id"]): int(item["capacity"])
            for item in config.get("resource_pools", [])
        })
        failures = {}
        for resource_id, amount in requested.items():
            used = sum(
                item.quantity
                for item in session.resource_reservations
                if item.resource_id == resource_id
                and item.status in {"reserved", "committed", "delivered"}
            )
            available = capacities[resource_id] - used
            if amount > available:
                failures[resource_id] = {
                    "required": amount,
                    "available": available,
                }
        cash = int(terms["cash_amount"])
        total_available = unencumbered_budget(session)
        if cash > total_available:
            failures["total_budget"] = {
                "required": cash,
                "available": total_available,
            }
        authorization_failures = self._authorization_limit_failures(
            session, contract, requested
        )
        failures.update(authorization_failures)
        if failures:
            raise ActionUnavailableError(
                "合同资源不足，不能预占",
                details={"resources": failures},
            )
        expires = min(89, session.game_state.story_day + 2)
        for resource_id, amount in requested.items():
            reservation = ResourceReservation(
                reservation_id=f"reserve_{secrets.token_hex(10)}",
                owner_type="contract",
                owner_id=contract.contract_id,
                resource_id=resource_id,
                quantity=amount,
                status="reserved",
                reserved_day=session.game_state.story_day,
                expires_day=expires,
            )
            session.resource_reservations.append(reservation)
            self._record_resource_event(
                session,
                change_kind="reservation",
                source_type="contract_review",
                source_id=contract.contract_id,
                resource_id=resource_id,
                quantity=amount,
                reservation_id=reservation.reservation_id,
                expires_day=expires,
                payment_status="unpaid",
            )

    def _validate_contract_reservations(
        self,
        session: GameSession,
        contract: HouseholdContract,
    ) -> None:
        """签署必须原子核对本版本合同对应的完整、未过期预占。"""

        expected = self._contract_resource_request(contract)
        actual: dict[str, int] = {}
        for reservation in session.resource_reservations:
            if (
                reservation.owner_type == "contract"
                and reservation.owner_id == contract.contract_id
                and reservation.status == "reserved"
                and (
                    reservation.expires_day is None
                    or reservation.expires_day >= session.game_state.story_day
                )
            ):
                actual[reservation.resource_id] = (
                    actual.get(reservation.resource_id, 0)
                    + reservation.quantity
                )
        if actual != expected:
            raise ActionUnavailableError(
                "合同资源预占不完整或已失效，请重新送审",
                details={"expected": expected, "reserved": actual},
            )

    def _authorization_limit_failures(
        self,
        session: GameSession,
        contract: HouseholdContract,
        requested: dict[str, int],
    ) -> dict[str, dict]:
        assert contract.term_sheet is not None
        failures: dict[str, dict] = {}
        for document_id in contract.term_sheet.get(
            "approval_document_ids", []
        ):
            document = session.administrative_documents.get(
                str(document_id)
            )
            if document is None:
                continue
            limits = {
                str(resource_id): int(amount)
                for resource_id, amount in document.resolution_snapshot.get(
                    "resource_authorization_limits", {}
                ).items()
            }
            if not limits:
                continue
            usage = self._document_authorization_usage(session, document)
            for resource_id, amount in requested.items():
                if resource_id not in limits:
                    continue
                remaining = limits[resource_id] - usage.get(resource_id, 0)
                if amount > remaining:
                    failures[
                        f"authorization:{document.document_id}:{resource_id}"
                    ] = {
                        "required": amount,
                        "available": max(0, remaining),
                        "document_id": document.document_id,
                        "authorized": limits[resource_id],
                        "already_drawn": usage.get(resource_id, 0),
                    }
        return failures

    def _document_authorization_usage(
        self,
        session: GameSession,
        document: AdministrativeDocument,
    ) -> dict[str, int]:
        contract_ids = {
            contract.contract_id
            for contract in session.household_contracts.values()
            if (
                contract.term_sheet is not None
                and document.document_id
                in contract.term_sheet.get("approval_document_ids", [])
            )
        }
        usage: dict[str, int] = {}
        for reservation in session.resource_reservations:
            if (
                reservation.owner_type == "contract"
                and reservation.owner_id in contract_ids
                and reservation.status in {
                    "reserved", "committed", "delivered",
                }
            ):
                usage[reservation.resource_id] = (
                    usage.get(reservation.resource_id, 0)
                    + reservation.quantity
                )
        return usage

    @staticmethod
    def _contract_resource_request(
        contract: HouseholdContract,
    ) -> dict[str, int]:
        if contract.term_sheet is None:
            return {}
        terms = contract.term_sheet
        return {
            f"budget:{terms['budget_envelope']}": int(terms["cash_amount"]),
            **({
                str(terms["housing_resource_id"]): 1
            } if terms.get("housing_resource_id") else {}),
            **{
                str(key): int(value)
                for key, value in terms["service_allocations"].items()
            },
        }

    def _missing_hard_conditions(
        self,
        session: GameSession,
        package: ScriptPackage,
        contract: HouseholdContract,
    ) -> list[str]:
        assert contract.term_sheet is not None
        household = self._household(package, contract.household_id)
        terms = contract.term_sheet
        allocations = set(terms["service_allocations"])
        missing = []
        if (
            household.grave_or_shrine_profile
            not in {"none", "clan_follower", "clan_accounting"}
            and "grave_relocation_service" not in allocations
        ):
            missing.append("迁坟事务资源未落实")
        if household.medical_tags and not allocations.intersection({
            "lead_recheck_slot",
            "child_assessment_slot",
            "emergency_referral_slot",
        }):
            missing.append("医疗复检或评估资源未落实")
        if (
            "school_continuity" in household.employment_startup_tags
            and "school_transition_seat" not in allocations
        ):
            missing.append("就学衔接资源未落实")
        if (
            household.ownership_status == "migrant_authorization_needed"
            and not terms["authorization_confirmed"]
        ):
            missing.append("外出户本人授权尚未核验")
        if (
            household.ownership_status == "ledger_sensitive"
            and not terms["ledger_disclosed"]
        ):
            missing.append("逐项测算账目尚未公开")
        if (
            household.ownership_status in {
                "old_road_case_pending", "old_materials_sensitive",
            }
            and not terms["old_case_resolved"]
        ):
            missing.append("历史旧案尚未形成书面处理结果")
        if (
            household.ownership_status == "prior_extra_payment_risk"
            and not terms["prior_payment_verified"]
        ):
            missing.append("既往额外付款尚未核验")
        if (
            household.resettlement_preference
            == "resettlement_house_must_see_real_unit"
            and not terms["real_unit_viewed"]
        ):
            missing.append("签约人尚未查看可交付实房")
        if (
            household.signing_lock_flag
            and household.signing_lock_flag not in session.flags
        ):
            missing.append(
                f"本户核心矛盾尚未解决（{household.signing_lock_flag}）"
            )
        return missing

    def _validate_contract_text(
        self,
        contract: HouseholdContract,
        term_sheet: dict,
        text: str,
        package: ScriptPackage,
    ) -> None:
        if not text:
            raise ActionUnavailableError("合同正文不能为空")
        missing = self._missing_contract_term_fields(
            contract, term_sheet, text
        )
        if self._RESOURCE_AUTHORITY_CLAUSE not in text:
            missing.append("结构化资源权威条款")
        if missing:
            raise ActionUnavailableError(
                "合同文本与结构化资源条款不一致",
                details={"missing_term_fields": missing},
            )
        expected_resource_ids = set(term_sheet["service_allocations"])
        if term_sheet.get("housing_resource_id"):
            expected_resource_ids.add(
                str(term_sheet["housing_resource_id"])
            )
        expected_resource_ids.add(
            f"budget:{term_sheet['budget_envelope']}"
        )
        self._validate_no_unstructured_commitments(
            text,
            structured={
                **term_sheet,
                "contract_id": contract.contract_id,
                "household_id": contract.household_id,
                "signatory_name": contract.signatory_name,
            },
            expected_resource_ids=expected_resource_ids,
            package=package,
        )

    @classmethod
    def _missing_contract_term_fields(
        cls,
        contract: HouseholdContract,
        term_sheet: dict,
        text: str,
    ) -> list[str]:
        checks: list[tuple[str, object]] = [
            ("contract_id", contract.contract_id),
            ("household_id", contract.household_id),
            ("signatory_name", contract.signatory_name),
            ("policy_document_id", term_sheet["policy_document_id"]),
            ("cash_amount", term_sheet["cash_amount"]),
            ("payment_day", term_sheet["payment_day"]),
            ("move_out_day", term_sheet["move_out_day"]),
            ("housing_delivery_day", term_sheet["housing_delivery_day"]),
        ]
        if term_sheet.get("housing_resource_id"):
            checks.append(
                ("housing_resource_id", term_sheet["housing_resource_id"])
            )
        checks.extend(
            (f"service_allocations.{resource_id}", resource_id)
            for resource_id in term_sheet["service_allocations"]
        )
        return [
            field_name
            for field_name, value in checks
            if not cls._contract_term_is_present(
                text, field_name, value
            )
        ]

    @staticmethod
    def _contract_term_is_present(
        text: str,
        field_name: str,
        value: object,
    ) -> bool:
        escaped = re.escape(str(value))
        if field_name == "cash_amount":
            amount_pattern = re.compile(
                rf"(?<![\d.]){escaped}(?:\.0+)?\s*万元"
            )
            context_pattern = re.compile(
                r"现金(?:补偿|权益|金额|补偿款)?|补偿(?:款|金额)|支付现金"
            )
            negated_prefix = re.compile(
                r"(?:非现金|非|无|未|不|不含|不作|无需|不得|"
                r"不予|不再|不另行|不额外|拒绝)\s*$"
            )
            for amount_match in amount_pattern.finditer(text):
                clause_start = max(
                    text.rfind(separator, 0, amount_match.start()) + 1
                    for separator in ("。", "；", ";", "\n")
                )
                clause_prefix = text[clause_start:amount_match.start()]
                for context_match in context_pattern.finditer(clause_prefix):
                    if len(clause_prefix) - context_match.end() > 24:
                        continue
                    marker = context_match.group(0)
                    prefix = clause_prefix[
                        max(0, context_match.start() - 8):
                        context_match.start()
                    ]
                    if marker.startswith("现金") and prefix.endswith("支付"):
                        continue
                    if negated_prefix.search(prefix):
                        continue
                    return True
            return False
        date_labels = {
            "payment_day": r"(?:付款|支付)",
            "move_out_day": r"(?:搬离|腾退|搬迁)",
            "housing_delivery_day": r"(?:交房|房源交付|安置房交付)",
        }
        if field_name in date_labels:
            return re.search(
                rf"{date_labels[field_name]}[^。\n]{{0,30}}D\s*{escaped}"
                rf"(?!\d)",
                text,
            ) is not None
        return str(value) in text

    def _audit_contract_version(
        self,
        session: GameSession,
        package: ScriptPackage,
        contract: HouseholdContract,
        version: ContractVersion,
        term_sheet: dict,
    ) -> None:
        policy = session.administrative_documents[
            str(term_sheet["policy_document_id"])
        ]
        result = self._gateway.run_governance_task(
            GovernanceLLMContext(
                session_id=session.session_id,
                account_id=session.account_id,
                operation_id=(
                    f"{contract.contract_id}:audit:v{version.version}:"
                    f"{version.text_hash}"
                ),
                story_day=session.game_state.story_day,
                task="audit_contract",
                actor_id="contract_auditor",
                actor_name="合同专业审校模型",
                actor_profile=(
                    "独立审校合同正文，只定位问题，不修改条款，"
                    "不代表签约人作出接受决定。"
                ),
                prompt_version="contract-audit-v1",
                payload={
                    "contract_identity": {
                        "contract_id": contract.contract_id,
                        "household_id": contract.household_id,
                        "signatory_name": contract.signatory_name,
                    },
                    "contract_text": version.text,
                    "term_sheet": term_sheet,
                    "policy_document": {
                        "document_id": policy.document_id,
                        "title": policy.title,
                        "content": policy.content,
                        "status": policy.status,
                    },
                    "resource_authority_clause": (
                        self._RESOURCE_AUTHORITY_CLAUSE
                    ),
                },
            )
        )
        audit = {
            "status": str(result.data["status"]),
            "summary": str(result.data["summary"]),
            "detected_commitments": list(
                result.data.get("detected_commitments", [])
            ),
            "issues": [
                dict(item) for item in result.data.get("issues", [])
            ],
        }
        deterministic_issues = self._deterministic_contract_audit_issues(
            contract, term_sheet, version.text, package
        )
        known_issue_ids = {
            str(item.get("issue_id"))
            for item in audit["issues"]
        }
        audit["issues"].extend(
            item for item in deterministic_issues
            if item["issue_id"] not in known_issue_ids
        )
        if deterministic_issues:
            audit["status"] = "reject"
            audit["summary"] = (
                "合同正文与结构化条款存在必须修正的一致性问题。"
            )
        elif audit["status"] == "pass" and audit["issues"]:
            audit["status"] = "needs_revision"
        version.audit_status = str(audit["status"])
        version.audit_result = audit
        version.audit_model_id = result.model_id
        version.audited_at = governance_now_iso()

    def _deterministic_contract_audit_issues(
        self,
        contract: HouseholdContract,
        term_sheet: dict,
        text: str,
        package: ScriptPackage,
    ) -> list[dict]:
        issues: list[dict] = []

        def add_issue(
            issue_id: str,
            *,
            category: str,
            term_field: str | None,
            message: str,
            text_quote: str,
            suggestion: str,
        ) -> None:
            issues.append({
                "issue_id": issue_id,
                "severity": "error",
                "category": category,
                "term_field": term_field,
                "message": message,
                "text_quote": text_quote,
                "suggestion": suggestion,
            })

        missing_fields = self._missing_contract_term_fields(
            contract, term_sheet, text
        )
        required_values = {
            "contract_id": contract.contract_id,
            "household_id": contract.household_id,
            "signatory_name": contract.signatory_name,
            "policy_document_id": term_sheet["policy_document_id"],
            "cash_amount": term_sheet["cash_amount"],
            "payment_day": term_sheet["payment_day"],
            "move_out_day": term_sheet["move_out_day"],
            "housing_delivery_day": term_sheet["housing_delivery_day"],
        }
        for field_name in missing_fields:
            if (
                field_name == "housing_resource_id"
                or field_name.startswith("service_allocations.")
            ):
                continue
            value = required_values.get(
                field_name, term_sheet.get(field_name)
            )
            add_issue(
                f"AUDIT-MISSING-{field_name}",
                category="missing_required_term",
                term_field=field_name,
                message=f"正文没有写明结构化字段 {field_name} 的值。",
                text_quote="（正文中未找到）",
                suggestion=f"在相应条款中明确写入：{value}。",
            )
        housing_id = term_sheet.get("housing_resource_id")
        if housing_id and str(housing_id) not in text:
            add_issue(
                "AUDIT-MISSING-housing_resource_id",
                category="missing_required_term",
                term_field="housing_resource_id",
                message="正文没有写明结构化附件指定的安置房资源。",
                text_quote="（正文中未找到）",
                suggestion=f"写明安置房资源：{housing_id}。",
            )
        for resource_id in term_sheet["service_allocations"]:
            if f"service_allocations.{resource_id}" in missing_fields:
                add_issue(
                    f"AUDIT-MISSING-service-{resource_id}",
                    category="missing_required_term",
                    term_field="service_allocations",
                    message="正文遗漏了一项结构化服务资源。",
                    text_quote="（正文中未找到）",
                    suggestion=f"写明服务资源：{resource_id}。",
                )
        if self._RESOURCE_AUTHORITY_CLAUSE not in text:
            add_issue(
                "AUDIT-MISSING-resource-authority-clause",
                category="missing_authority_clause",
                term_field=None,
                message="正文缺少结构化资源权威条款。",
                text_quote="（正文中未找到）",
                suggestion=f"加入：{self._RESOURCE_AUTHORITY_CLAUSE}",
            )
        expected_resource_ids = set(term_sheet["service_allocations"])
        if housing_id:
            expected_resource_ids.add(str(housing_id))
        expected_resource_ids.add(
            f"budget:{term_sheet['budget_envelope']}"
        )
        extra = self._unstructured_commitment_details(
            text,
            structured={
                **term_sheet,
                "contract_id": contract.contract_id,
                "household_id": contract.household_id,
                "signatory_name": contract.signatory_name,
            },
            expected_resource_ids=expected_resource_ids,
            package=package,
        )
        for resource_id in extra["resource_ids"]:
            add_issue(
                f"AUDIT-EXTRA-RESOURCE-{resource_id}",
                category="unstructured_commitment",
                term_field=None,
                message="正文引用了结构化附件之外的资源。",
                text_quote=resource_id,
                suggestion="删除该资源承诺，或先把它纳入结构化资源条款。",
            )
        for number in extra["numbers"]:
            add_issue(
                f"AUDIT-EXTRA-NUMBER-{number}",
                category="unstructured_commitment",
                term_field=None,
                message="正文出现了结构化附件之外的数值。",
                text_quote=number,
                suggestion="删除该数值承诺，或先修改结构化条款。",
            )
        return issues

    def _standard_cash(
        self,
        package: ScriptPackage,
        household: HouseholdDefinition,
        *,
        months: int,
        reward: bool,
    ) -> int:
        rates = (package.governance_config or {})["compensation_rates"]
        structure_rate = float(
            rates["residential_structure"][household.residential_structure]
        )
        total = (
            household.legal_residential_area_m2 * structure_rate
            + household.homestead_recognized_m2
            * float(rates["homestead_recognized_m2"])
            + household.contracted_land_mu
            * float(rates["contracted_land_mu"])
            + float(rates["moving_per_household"])
            + household.resettlement_population
            * months
            * float(rates["transition_per_person_month"])
            + (float(rates["public_window_reward"]) if reward else 0)
        )
        return math.ceil(total)

    def _archive_contract_draft(
        self, session: GameSession, contract: HouseholdContract
    ) -> None:
        archive_id = f"archive:contract:{contract.contract_id}:drafts"
        session.archive_records[archive_id] = ArchiveRecord(
            archive_id=archive_id,
            category="逐户合同",
            title=f"{contract.household_id}合同草案与版本记录",
            content=json.dumps(
                {
                    "term_sheet": contract.term_sheet,
                    "versions": [asdict(item) for item in contract.versions],
                    "review_history": contract.review_history,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            source_type="household_contract",
            source_id=contract.contract_id,
            acquired_day=session.game_state.story_day,
            acquired_via="contract_drafting",
            evidence_level="E1",
            confidentiality="private",
        )
        contract.archive_id = archive_id

    def _release_contract_reservations(
        self,
        session: GameSession,
        contract_id: str,
        *,
        reason: str,
    ) -> None:
        for reservation in session.resource_reservations:
            if (
                reservation.owner_type == "contract"
                and reservation.owner_id == contract_id
                and reservation.status == "reserved"
            ):
                reservation.status = "released"
                self._record_resource_event(
                    session,
                    change_kind="release",
                    source_type="contract_review",
                    source_id=contract_id,
                    resource_id=reservation.resource_id,
                    quantity=reservation.quantity,
                    reservation_id=reservation.reservation_id,
                    release_reason=reason,
                    payment_status="unpaid",
                )

    @staticmethod
    def _record_resource_event(
        session: GameSession,
        *,
        change_kind: str,
        source_type: str,
        source_id: str,
        resource_id: str,
        quantity: int,
        **details,
    ) -> None:
        session.resource_ledger_entries.append({
            "entry_id": (
                f"resource:{session.game_state.story_day}:"
                f"{len(session.resource_ledger_entries) + 1}"
            ),
            "story_day": session.game_state.story_day,
            "change_kind": change_kind,
            "source_type": source_type,
            "source_id": source_id,
            "resource_id": resource_id,
            "quantity": quantity,
            **details,
        })

    def _contract_actor(
        self, package: ScriptPackage, contract: HouseholdContract
    ) -> tuple[str, str, str]:
        if contract.signatory_npc_id:
            profile = next(
                item for item in package.npc_profiles
                if item.npc_id == contract.signatory_npc_id
            )
            return profile.npc_id, profile.name, profile.role_setting
        limited = package.limited_signatory_for(contract.household_id)
        assert limited is not None
        profile = (
            f"初始立场：{limited.initial_position}；"
            f"核心关切：{limited.core_concern}；"
            f"接受条件：{limited.acceptance_condition}；"
            f"拒绝触发：{limited.refusal_trigger}；"
            f"反报价方向：{limited.counteroffer_focus}。"
        )
        return f"signatory:{contract.household_id}", limited.name, profile

    def _base_actions(
        self, session: GameSession, package: ScriptPackage
    ) -> list[dict]:
        config = package.governance_config or {}
        active = any(
            item.status == "active"
            for item in session.governance_actions.values()
        )
        result = []
        for item in config.get("base_actions", []):
            cost = int(item["cost"])
            result.append({
                **item,
                "available": (
                    session.status is SessionStatus.ACTIVE
                    and not active
                    and session.processing_action_id is None
                    and session.pending_decision is None
                    and session.active_conversation is None
                    and session.active_group_conversation is None
                    and session.game_state.action_points >= cost
                ),
                "unavailable_reason": (
                    "已有场景或决策正在进行"
                    if active
                    or session.processing_action_id is not None
                    or session.pending_decision is not None
                    or session.active_conversation is not None
                    or session.active_group_conversation is not None
                    else (
                        "当日行动点不足"
                        if session.game_state.action_points < cost else None
                    )
                ),
            })
        return result

    def _resource_status(
        self, session: GameSession, package: ScriptPackage
    ) -> dict:
        config = package.governance_config or {}

        def totals(resource_id: str) -> dict[str, int]:
            values = {
                "reserved": 0,
                "committed": 0,
                "delivered": 0,
            }
            for reservation in session.resource_reservations:
                if (
                    reservation.resource_id == resource_id
                    and reservation.status in values
                ):
                    values[reservation.status] += reservation.quantity
            return values

        pools = []
        for item in config.get("resource_pools", []):
            status_totals = totals(str(item["resource_id"]))
            blocked = sum(status_totals.values())
            pools.append({
                **item,
                **status_totals,
                "blocked_total": blocked,
                "available_to_reserve": int(item["capacity"]) - blocked,
                "used": blocked,
                "available": int(item["capacity"]) - blocked,
            })
        envelopes = {}
        for envelope_id, capacity in config.get(
            "budget_envelopes", {}
        ).items():
            resource_id = f"budget:{envelope_id}"
            status_totals = totals(resource_id)
            blocked = sum(status_totals.values())
            envelopes[envelope_id] = {
                "capacity": int(capacity),
                **status_totals,
                "blocked_total": blocked,
                "available_to_reserve": int(capacity) - blocked,
                "used": blocked,
                "available": int(capacity) - blocked,
            }
        active_reservations = [
            {
                **asdict(item),
                "display_status": (
                    f"预占至D{item.expires_day}，尚未支付"
                    if item.status == "reserved"
                    else "已签署并占用资源，尚未支付"
                    if item.status == "committed"
                    else "已支付"
                    if item.resource_id.startswith("budget:")
                    else "已完成交付"
                ),
            }
            for item in session.resource_reservations
            if item.status in {"reserved", "committed", "delivered"}
        ]
        return {
            "cash_ledger": {
                "remaining": session.game_state.budget_remaining,
                "available_unencumbered": unencumbered_budget(session),
                "committed": active_budget_holds(
                    session,
                    statuses=frozenset({"committed"}),
                ),
                "contract_committed_cumulative": (
                    session.game_state.budget_committed
                ),
                "paid": session.game_state.budget_paid,
                "approved_adjustments": (
                    session.game_state.budget_approved_adjustments
                ),
                "outstanding": active_budget_holds(
                    session,
                    statuses=frozenset({"committed"}),
                ),
            },
            "budget_envelopes": envelopes,
            "resource_pools": pools,
            "active_reservations": active_reservations,
        }

    def _meeting_decision_mode(
        self, package: ScriptPackage, document_type: str | None
    ) -> str:
        if not document_type:
            return "executive_decision"
        return str(
            (package.governance_config or {})["document_rules"][
                document_type
            ]["decision_mode"]
        )

    def _load(
        self, account_id: str, session_id: str
    ) -> tuple[GameSession, ScriptPackage]:
        session = self._sessions.get_owned(session_id, account_id)
        if session is None:
            raise NotFoundError("游戏不存在")
        package = require_locked_package(self._packages, session)
        return session, package

    def _load_mutable(
        self, account_id: str, session_id: str, state_version: int
    ) -> tuple[GameSession, ScriptPackage]:
        session, package = self._load(account_id, session_id)
        if session.status is not SessionStatus.ACTIVE:
            raise SessionEndedError("当前游戏不可继续写入")
        if session.state_version != state_version:
            raise StateVersionConflictError(
                "状态版本已变化，请刷新后重试",
                details={"current_state_version": session.state_version},
            )
        if session.processing_action_id is not None:
            raise ActionUnavailableError("上一操作仍在处理中")
        return session, package

    def _commit(self, session: GameSession, expected_version: int) -> None:
        session.state_version += 1
        session.touch()
        self._sessions.save(session, expected_version=expected_version)

    @staticmethod
    def _meeting(session: GameSession, meeting_id: str) -> MeetingRecord:
        value = session.meetings.get(meeting_id)
        if value is None:
            raise NotFoundError("班子会议不存在")
        return value

    @staticmethod
    def _document(
        session: GameSession, document_id: str
    ) -> AdministrativeDocument:
        value = session.administrative_documents.get(document_id)
        if value is None:
            raise NotFoundError("红头文件不存在")
        return value

    @staticmethod
    def _contract(
        session: GameSession, contract_id: str
    ) -> HouseholdContract:
        value = session.household_contracts.get(contract_id)
        if value is None:
            raise NotFoundError("逐户合同不存在")
        return value

    @staticmethod
    def _household(
        package: ScriptPackage, household_id: str
    ) -> HouseholdDefinition:
        value = next(
            (
                item for item in package.households
                if item.household_id == household_id
            ),
            None,
        )
        if value is None:
            raise NotFoundError("家庭底账不存在")
        return value

    @staticmethod
    def _current_contract_text(contract: HouseholdContract) -> str:
        return GameplayGovernanceService._current_contract_version(
            contract
        ).text

    @staticmethod
    def _current_contract_version(
        contract: HouseholdContract,
    ) -> ContractVersion:
        if not contract.versions or contract.current_version <= 0:
            raise ActionUnavailableError("合同尚无正文版本")
        return next(
            item for item in contract.versions
            if item.version == contract.current_version
        )

    @staticmethod
    def _public_archive(
        value: ArchiveRecord, *, include_content: bool = False
    ) -> dict:
        result = {
            "archive_id": value.archive_id,
            "category": value.category,
            "title": value.title,
            "source_type": value.source_type,
            "source_id": value.source_id,
            "acquired_day": value.acquired_day,
            "acquired_via": value.acquired_via,
            "evidence_level": value.evidence_level,
            "confidentiality": value.confidentiality,
            "read_at_days": list(value.read_at_days),
            "related_npc_ids": list(value.related_npc_ids),
        }
        if include_content:
            result["content"] = value.content
        return result

    def _public_document(
        self,
        value: AdministrativeDocument,
        *,
        session: GameSession | None = None,
    ) -> dict:
        result = {
            "document_id": value.document_id,
            "document_type": value.document_type,
            "title": value.title,
            "status": value.status,
            "version": value.version,
            "content": value.content,
            "story_day": value.story_day,
            "policy_version": value.policy_version,
            "source_meeting_id": value.source_meeting_id,
            "resolution_snapshot": value.resolution_snapshot,
            "required_countersign_ids": list(value.required_countersign_ids),
            "countersigned_by": list(value.countersigned_by),
            "public_scope": list(value.public_scope),
            "publication_records": value.publication_records,
            "content_hash": value.content_hash,
            "issued_day": value.issued_day,
            "archive_id": value.archive_id,
            "review_status": value.review_status,
            "review_summary": value.review_summary,
            "review_model_id": value.review_model_id,
            "reviewed_at": value.reviewed_at,
            "review_history": value.review_history,
            "revision_history": value.revision_history,
            "version_history": [
                {
                    key: item[key]
                    for key in (
                        "version", "content_hash", "created_by", "model_id",
                        "change_summary", "created_at",
                    )
                    if key in item
                }
                for item in value.version_history
            ],
        }
        limits = {
            str(resource_id): int(amount)
            for resource_id, amount in value.resolution_snapshot.get(
                "resource_authorization_limits", {}
            ).items()
        }
        usage = (
            self._document_authorization_usage(session, value)
            if session is not None and limits else {}
        )
        result["authorization_status"] = {
            resource_id: {
                "authorized": amount,
                "drawn": usage.get(resource_id, 0),
                "remaining": max(
                    0, amount - usage.get(resource_id, 0)
                ),
            }
            for resource_id, amount in limits.items()
        }
        return result

    @staticmethod
    def _public_meeting(value: MeetingRecord) -> dict:
        return {
            "meeting_id": value.meeting_id,
            "action_instance_id": value.action_instance_id,
            "story_day": value.story_day,
            "topic": value.topic,
            "participant_ids": list(value.participant_ids),
            "lead_npc_id": value.lead_npc_id,
            "speaking_order": [
                value.lead_npc_id,
                *(
                    npc_id for npc_id in value.participant_ids
                    if npc_id != value.lead_npc_id
                ),
            ],
            "decision_mode": value.decision_mode,
            "proposed_document_type": value.proposed_document_type,
            "transcript": value.transcript,
            "positions": value.positions,
            "resolution": value.resolution,
            "status": value.status,
        }

    def _public_contract(
        self, value: HouseholdContract, *, include_text: bool = False
    ) -> dict:
        current_version = (
            self._current_contract_version(value)
            if value.versions and value.current_version > 0
            else None
        )
        if value.fulfillment.get("cash_paid"):
            hold_status = "已支付"
        elif value.status == "signed":
            hold_status = "已签署并占用资源，尚未支付"
        elif value.status == "accepted" and value.reserved_until_day is not None:
            hold_status = (
                f"预占至D{value.reserved_until_day}，尚未支付"
            )
        else:
            hold_status = "未预占"
        result = {
            "contract_id": value.contract_id,
            "batch_id": value.batch_id,
            "household_id": value.household_id,
            "signatory_name": value.signatory_name,
            "status": value.status,
            "term_sheet": value.term_sheet,
            "current_version": value.current_version,
            "audit_status": (
                current_version.audit_status
                if current_version is not None else "not_started"
            ),
            "audit_result": (
                current_version.audit_result
                if current_version is not None else {}
            ),
            "audit_model_id": (
                current_version.audit_model_id
                if current_version is not None else None
            ),
            "audited_at": (
                current_version.audited_at
                if current_version is not None else None
            ),
            "review_decision": value.review_decision,
            "review_reason": value.review_reason,
            "counteroffer": value.counteroffer,
            "review_history": value.review_history,
            "reserved_until_day": value.reserved_until_day,
            "resource_hold_status": hold_status,
            "signed_day": value.signed_day,
            "signed_hash": value.signed_hash,
            "archive_id": value.archive_id,
            "fulfillment": value.fulfillment,
        }
        if include_text and value.versions:
            result["contract_text"] = self._current_contract_text(value)
            result["versions"] = [asdict(item) for item in value.versions]
        return result

    @staticmethod
    def _hash(value: object) -> str:
        text = value if isinstance(value, str) else json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
