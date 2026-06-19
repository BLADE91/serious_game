"""章节剧本的领域模型。

用于新的 6-Call 章节式生成管线。
"""

from dataclasses import dataclass, field
from typing import Any


# ---- 节点 ----

@dataclass(frozen=True)
class ChapterNode:
    """章节流程图中的一个节点。"""

    node_id: str
    node_type: str  # INFO | CHOICE | RESULT | CHECKPOINT
    title: str
    text: str = ""
    next: list[str] = field(default_factory=list)
    unlock_condition: dict | None = None
    # unlock_condition 格式: {"flags_required": [...], "flags_forbidden": [...], "variables": {...}}


# ---- 选项 ----

@dataclass(frozen=True)
class ChapterOption:
    """核心决策点中的一个选项。"""

    choice_id: str
    option_label: str  # A / B / C / D
    text: str  # 玩家看到的一句话描述（20-40 字）
    availability: dict | None = None
    # availability 格式: {"variables": {...}, "flags_required": [...], "flags_forbidden": [...]}
    effects: dict[str, int] = field(default_factory=lambda: {
        "signed": 0, "social_stability": 0, "political_credit": 0,
        "public_trust": 0, "env_clue": 0, "media_pressure": 0,
        "budget": 0, "days_left": 0,
    })
    npc_state_changes: dict[str, dict[str, Any]] = field(default_factory=dict)
    unlock_nodes: list[str] = field(default_factory=list)
    lock_nodes: list[str] = field(default_factory=list)
    flags_added: list[str] = field(default_factory=list)
    flags_removed: list[str] = field(default_factory=list)
    immediate_result_text: str = ""
    long_term_effect: str = ""
    teaching_feedback: str = ""


# ---- 决策点 ----

@dataclass(frozen=True)
class ChapterDecisionPoint:
    """章节中的一个核心决策点。每章可有 2-4 个，顺序执行。"""

    node_id: str
    question: str
    order: int = 1  # 本章内的顺序编号（1-based）
    options: list[ChapterOption] = field(default_factory=list)


# ---- 结果节点 ----

@dataclass(frozen=True)
class ChapterResult:
    """一个选项对应的叙事结果。"""

    node_id: str
    from_choice: str
    text: str
    next: str  # 汇流到 checkpoint


# ---- 章节结算 ----

@dataclass(frozen=True)
class ChapterCheckpoint:
    """章节汇流点。"""

    checkpoint_id: str
    merge_from: list[str] = field(default_factory=list)
    next_chapter: str = ""
    summary: str = ""


# ---- 状态快照（嵌入在 checkpoint 中）----

@dataclass(frozen=True)
class ChapterStateSnapshot:
    """章节结算时的变量和 flag 状态。

    这是跨章节状态传递的关键数据结构。
    """

    # 8 个全局变量
    signed: int = 0
    social_stability: int = 70
    political_credit: int = 70
    public_trust: int = 50
    env_clue: int = 0
    media_pressure: int = 30
    budget: int = 8000
    days_left: int = 90

    # Flag 系统
    active_flags: set[str] = field(default_factory=set)
    unlocked_nodes: set[str] = field(default_factory=set)
    locked_nodes: set[str] = field(default_factory=set)

    # 变量范围（最低/最高/最可能路径）
    variable_ranges: dict[str, dict[str, int]] = field(default_factory=dict)
    # 格式: {"signed": {"min": 0, "max": 5, "most_likely": 3}, ...}


# ---- 章节 ----

@dataclass(frozen=True)
class Chapter:
    """一个完整章节。"""

    chapter_id: str  # ch01, ch02, ...
    order: int  # 1-based
    title: str
    day_range: str
    core_task: str
    main_question: str
    learning_goals: list[str] = field(default_factory=list)
    unlock_condition: dict | None = None

    # 内容
    background: str = ""  # 背景情境（含条件文本段）

    # 子结构
    info_nodes: list[ChapterNode] = field(default_factory=list)
    decision_points: list[ChapterDecisionPoint] = field(default_factory=list)
    results: list[ChapterResult] = field(default_factory=list)
    checkpoint: ChapterCheckpoint | None = None
    state_snapshot: ChapterStateSnapshot | None = None


# ---- 结局 ----

@dataclass(frozen=True)
class ChapterEnding:
    """剧本结局。"""

    ending_id: str
    title: str
    ending_type: str  # good / neutral / bad
    priority: int = 0  # 优先级，数字越小越高
    description: str = ""
    conditions: dict = field(default_factory=lambda: {
        "variables": {},
        "flags_required": [],
        "flags_forbidden": [],
    })
    ending_text: str = ""
    teaching_summary: str = ""


# ---- 全局设定 ----

@dataclass(frozen=True)
class GameSettings:
    """Call 1 输出的全局设定。"""

    title: str = ""
    player_role: str = ""
    core_conflict: str = ""
    learning_goals: list[str] = field(default_factory=list)
    chapter_count: int = 6
    ending_count: int = 4
    style: str = ""
    npcs: list[dict] = field(default_factory=list)
    endings: list[dict] = field(default_factory=list)
    variable_initial_values: dict[str, int] = field(default_factory=lambda: {
        "signed": 0, "social_stability": 70, "political_credit": 70,
        "public_trust": 50, "env_clue": 0, "media_pressure": 30,
        "budget": 8000, "days_left": 90,
    })


# ---- 章节大纲 ----

@dataclass(frozen=True)
class ChapterOutline:
    """Call 2 输出的章节大纲。"""

    ending_reachability: list[dict] = field(default_factory=list)
    chapters: list[dict] = field(default_factory=list)
    flag_global_plan: list[dict] = field(default_factory=list)
