from __future__ import annotations

from dataclasses import dataclass
import os

from serious_game_backend.application.action_service import ActionService
from serious_game_backend.application.end_day_service import EndDayService
from serious_game_backend.application.ending_service import EndingAxisProjector, EndingService
from serious_game_backend.application.event_service import EventService
from serious_game_backend.application.game_session_service import GameSessionService
from serious_game_backend.application.interaction_opportunity_service import (
    InteractionOpportunityService,
)
from serious_game_backend.application.map_service import MapService
from serious_game_backend.application.night_simulation_service import NightSimulationService
from serious_game_backend.application.package_validation_service import (
    PackageValidationService,
)
from serious_game_backend.application.review_service import ReviewService
from serious_game_backend.application.npc_turn_service import NPCTurnService
from serious_game_backend.application.npc_memory_service import NPCMemoryService
from serious_game_backend.application.ports import (
    GameSessionRepository,
    OperationRepository,
    SessionRequestRepository,
)
from serious_game_backend.application.scripted_delta_resolver import ScriptedDeltaResolver
from serious_game_backend.application.scripted_effect_service import ScriptedEffectService
from serious_game_backend.application.state_delta_validator import StateDeltaValidator
from serious_game_backend.application.story_clock_service import StoryClockService
from serious_game_backend.application.story_flow_service import StoryFlowService
from serious_game_backend.application.visible_state import VisibleStateProjector
from serious_game_backend.config import Settings
from serious_game_backend.infrastructure.llm.fake import FakeRoleLLMGateway
from serious_game_backend.infrastructure.llm.openai_compatible import OpenAICompatibleRoleLLMGateway
from serious_game_backend.infrastructure.repositories.memory import (
    InMemoryGameSessionRepository,
    InMemoryOperationRepository,
    InMemoryRuntimeTransactionRepository,
    InMemoryScriptPackageRepository,
    InMemorySessionRequestRepository,
    InMemoryLLMCallAuditRepository,
    InMemoryNPCMemoryRepository,
)
from serious_game_backend.infrastructure.repositories.sqlite import (
    SqliteGameSessionRepository,
    SqliteOperationRepository,
    SqliteRuntimeTransactionRepository,
    SqliteRuntimeStore,
    SqliteSessionRequestRepository,
    SqliteLLMCallAuditRepository,
    SqliteNPCMemoryRepository,
)
from serious_game_backend.infrastructure.script_packages.file_loader import FileScriptPackageLoader


@dataclass(slots=True)
class Container:
    settings: Settings
    sessions: GameSessionRepository
    operations: OperationRepository
    session_requests: SessionRequestRepository
    packages: InMemoryScriptPackageRepository
    projector: VisibleStateProjector
    game_sessions: GameSessionService
    actions: ActionService
    end_days: EndDayService
    npc_turns: NPCTurnService
    opportunities: InteractionOpportunityService
    story_flow: StoryFlowService
    map_service: MapService
    review_service: ReviewService
    package_validation: PackageValidationService
    endings: EndingService
    llm_audits: object
    npc_memories: NPCMemoryService


def build_container(settings: Settings) -> Container:
    if settings.repository == "mysql":
        raise RuntimeError(
            "MySQL schema 已建立，但 MySQL repository adapter 尚未接入；"
            "当前里程碑请使用 GAME_REPOSITORY=sqlite"
        )
    loader = FileScriptPackageLoader()
    package_values = loader.load_all(settings.content_root)
    if settings.repository == "sqlite":
        store = SqliteRuntimeStore(settings.database_path)
        sessions = SqliteGameSessionRepository(store)
        operations = SqliteOperationRepository(store)
        session_requests = SqliteSessionRequestRepository(store)
        transactions = SqliteRuntimeTransactionRepository(store)
        llm_audits = SqliteLLMCallAuditRepository(store)
        memory_repository = SqliteNPCMemoryRepository(store)
    else:
        sessions = InMemoryGameSessionRepository()
        operations = InMemoryOperationRepository()
        session_requests = InMemorySessionRequestRepository()
        transactions = InMemoryRuntimeTransactionRepository(
            sessions, operations, session_requests
        )
        llm_audits = InMemoryLLMCallAuditRepository()
        memory_repository = InMemoryNPCMemoryRepository()
    packages = InMemoryScriptPackageRepository(package_values)
    projector = VisibleStateProjector()
    event_service = EventService()
    story_clock = StoryClockService(event_service)
    fake_llm = FakeRoleLLMGateway()
    if settings.role_llm_provider == "openai_compatible":
        role_llm = OpenAICompatibleRoleLLMGateway(
            settings,
            os.getenv(settings.role_llm_api_key_env, ""),
            llm_audits,
            fallback=fake_llm,
        )
    else:
        role_llm = fake_llm
    delta_resolver = ScriptedDeltaResolver()
    validator = StateDeltaValidator(delta_resolver)
    scripted_effects = ScriptedEffectService(delta_resolver)
    nights = NightSimulationService(scripted_effects)
    endings = EndingService(EndingAxisProjector())
    npc_turns = NPCTurnService(role_llm, validator)
    npc_memories = NPCMemoryService(memory_repository)
    opportunities = InteractionOpportunityService()
    story_flow = StoryFlowService()
    return Container(
        settings=settings,
        sessions=sessions,
        operations=operations,
        session_requests=session_requests,
        packages=packages,
        projector=projector,
        game_sessions=GameSessionService(
            sessions,
            session_requests,
            transactions,
            packages,
            story_flow,
            event_service,
        ),
        actions=ActionService(
            sessions,
            operations,
            transactions,
            packages,
            projector,
            opportunities,
            npc_turns,
            scripted_effects,
            story_flow,
            npc_memories,
        ),
        end_days=EndDayService(
            sessions,
            operations,
            transactions,
            packages,
            story_clock,
            nights,
            endings,
            projector,
            story_flow,
        ),
        npc_turns=npc_turns,
        opportunities=opportunities,
        story_flow=story_flow,
        map_service=MapService(opportunities),
        review_service=ReviewService(projector),
        package_validation=PackageValidationService(),
        endings=endings,
        llm_audits=llm_audits,
        npc_memories=npc_memories,
    )
