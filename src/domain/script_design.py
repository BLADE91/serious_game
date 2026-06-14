"""剧本生成器的结构化输出。"""

from dataclasses import dataclass, field
from typing import Any

from src.domain.game_action import GameActionRule
from src.domain.game_state import GameState
from src.domain.npc_state import NPCState
from src.domain.source_context import SourceContext


@dataclass(frozen=True)
class ScriptCitation:
    """剧本字段对应的来源引用。"""

    citation_id: str
    source_context_id: str
    title: str
    note: str


@dataclass(frozen=True)
class ScriptEventOutline:
    """剧本初稿中的事件概要。"""

    event_id: str
    name: str
    day_window: str
    trigger_condition: str
    description: str
    payoff: dict[str, Any] = field(default_factory=dict)
    citations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ScriptDesign:
    """面向规则模块和人工修改的剧本初稿。"""

    title: str
    premise: str
    player_role: str
    core_conflict: str
    initial_game_state: GameState
    npc_seed: list[NPCState] = field(default_factory=list)
    action_rules: list[GameActionRule] = field(default_factory=list)
    event_outline: list[ScriptEventOutline] = field(default_factory=list)
    night_rules: list[str] = field(default_factory=list)
    payoff_notes: list[str] = field(default_factory=list)
    citations: list[ScriptCitation] = field(default_factory=list)


@dataclass(frozen=True)
class ScriptGenerationRequest:
    """剧本生成请求。"""

    query: str
    manual_queries: list[str] = field(default_factory=list)
    feedback: str = ""
    max_contexts: int = 4

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("ScriptGenerationRequest.query must not be empty")


@dataclass(frozen=True)
class ScriptGenerationResult:
    """剧本生成结果。"""

    script: ScriptDesign
    contexts_used: list[SourceContext] = field(default_factory=list)
    rewritten_queries: list[str] = field(default_factory=list)
    generation_notes: list[str] = field(default_factory=list)
    original_query: str = ""
    feedback: str = ""
    revision_round: int = 0
