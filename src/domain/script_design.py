"""剧本生成器的结构化输出。"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.domain.act_structure import ActStructure
from src.domain.decision_point import DecisionPoint
from src.domain.ending_condition import EndingCondition
from src.domain.game_action import GameActionRule
from src.domain.game_state import GameState
from src.domain.npc_relationship import NPCRelationship
from src.domain.npc_state import NPCState
from src.domain.source_context import SourceContext

if TYPE_CHECKING:
    from src.domain.chapter_structure import Chapter


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
    # --- 旧字段（保留向后兼容，新生成器不再填充）---
    npc_seed: list[NPCState] = field(default_factory=list)
    action_rules: list[GameActionRule] = field(default_factory=list)
    event_outline: list[ScriptEventOutline] = field(default_factory=list)
    night_rules: list[str] = field(default_factory=list)
    payoff_notes: list[str] = field(default_factory=list)
    citations: list[ScriptCitation] = field(default_factory=list)
    # --- 新字段（Phase 2 生成器填充）---
    npc_relationships: list[NPCRelationship] = field(default_factory=list)
    decision_points: list[DecisionPoint] = field(default_factory=list)
    acts: list[ActStructure] = field(default_factory=list)
    endings: list[EndingCondition] = field(default_factory=list)
    # --- 章节式生成字段（6-Call 管线填充）---
    chapters: list[Any] = field(default_factory=list)  # list[Chapter]


@dataclass(frozen=True)
class ScriptGenerationRequest:
    """剧本生成请求。

    支持两种输入方式：
    1. 结构化输入（推荐）：填写 scenario + player_role + learning_goal
    2. 自由文本输入（兼容旧版）：填写 query
    """

    # --- 新结构化字段 ---
    scenario: str = ""           # 政策场景，如 "生态搬迁"
    player_role: str = ""        # 玩家角色，如 "乡镇党委副书记"
    learning_goal: str = ""      # 教育目标，如 "体验基层政策执行中的多重压力"
    duration_minutes: int = 45   # 目标游戏时长
    extra_requirements: str = "" # 额外要求，自由文本
    npc_count: int = 12           # NPC 数量
    character_settings: str = ""   # 人物设定
    story_background: str = ""     # 故事背景
    # --- 章节式生成字段 ---
    chapter_count: int = 6         # 章节数量（6-Call 管线使用）
    ending_count: int = 4         # 结局数量（6-Call 管线使用）
    # --- 旧字段（保留兼容）---
    query: str = ""
    manual_queries: list[str] = field(default_factory=list)
    feedback: str = ""
    max_contexts: int = 4
    full_draft: bool = True

    def __post_init__(self) -> None:
        has_structured = bool(self.scenario.strip() and self.player_role.strip())
        has_query = bool(self.query.strip())
        if not has_structured and not has_query:
            raise ValueError(
                "ScriptGenerationRequest must have either (scenario + player_role) "
                "or query"
            )
        if self.duration_minutes < 10 or self.duration_minutes > 120:
            raise ValueError("duration_minutes must be 10-120")


@dataclass(frozen=True)
class ScriptGenerationResult:
    """剧本生成结果。"""

    script: ScriptDesign
    full_md: str = ""
    contexts_used: list[SourceContext] = field(default_factory=list)
    rewritten_queries: list[str] = field(default_factory=list)
    generation_notes: list[str] = field(default_factory=list)
    original_query: str = ""
    feedback: str = ""
    revision_round: int = 0
    generation_mode: str = "compact"
