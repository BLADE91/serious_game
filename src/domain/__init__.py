"""严肃游戏后端的领域模型。"""

from src.domain.act_structure import ActStructure
from src.domain.agent_generation import AgentGenerationRequest, AgentGenerationResult
from src.domain.decision_point import DecisionOption, DecisionPoint
from src.domain.ending_condition import EndingCondition
from src.domain.game_action import ActionResult, GameActionRule
from src.domain.game_state import GameState
from src.domain.npc_agent import NpcAgentConfig
from src.domain.npc_relationship import NPCRelationship
from src.domain.npc_state import NPCState
from src.domain.script_design import (
    ScriptCitation,
    ScriptDesign,
    ScriptEventOutline,
    ScriptGenerationRequest,
    ScriptGenerationResult,
)
from src.domain.simulation_log import SimulationLog
from src.domain.source_context import SourceContext

__all__ = [
    "ActStructure",
    "ActionResult",
    "AgentGenerationRequest",
    "AgentGenerationResult",
    "DecisionOption",
    "DecisionPoint",
    "EndingCondition",
    "GameActionRule",
    "GameState",
    "NPCState",
    "NPCRelationship",
    "NpcAgentConfig",
    "ScriptCitation",
    "ScriptDesign",
    "ScriptEventOutline",
    "ScriptGenerationRequest",
    "ScriptGenerationResult",
    "SimulationLog",
    "SourceContext",
]
