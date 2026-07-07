"""6-Call 章节式剧本生成的 Prompt 构建函数。

Call 1-3 使用 PA Backend 创作，Call 4 检查事实连续性，Call 5 使用 Qwen Flash 抽取，Call 6 使用程序规则校验。
只有 Call 1-3 的 Prompt 末尾需要「注意：不要给建议」指令，
因为 PA Backend 的 LLM 倾向于在输出末尾给出下一步建议或向用户提问。
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from src.generation.qwen_client import ChatMessage

if TYPE_CHECKING:
    from src.domain.chapter_structure import ChapterStateSnapshot

# ---- 常量 ----

VARIABLE_NAMES = [
    "signed", "social_stability", "political_credit", "public_trust",
    "env_clue", "media_pressure", "budget", "days_left",
]

VARIABLE_DEFAULTS = {
    "signed": 0, "social_stability": 70, "political_credit": 70,
    "public_trust": 50, "env_clue": 0, "media_pressure": 30,
    "budget": 8000, "days_left": 90,
}


def _pa_message(content: str) -> list[ChatMessage]:
    """构建 PA Backend 用的单条 user 消息（PA Backend 不使用 system/user 区分）。"""
    return [ChatMessage(role="user", content=content)]


def _flash_messages(system: str, user: str) -> list[ChatMessage]:
    """构建 Qwen Flash 用的 system + user 消息对。"""
    return [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=user),
    ]


# ============================================================
# Call 1: 生成全局设定
# ============================================================

def build_call1_prompt(
    scenario: str,
    player_role: str,
    learning_goals: list[str],
    chapter_count: int,
    ending_count: int,
    duration_minutes: int = 45,
    character_settings: str = "",
    story_background: str = "",
    extra_requirements: str = "",
    npc_count: int = 8,
    decision_point_count: int = 3,
) -> list[ChatMessage]:
    """构建 Call 1（全局设定）的 Prompt。"""

    goals_text = "\n".join(f"  - {g}" for g in learning_goals) if learning_goals else "  - （由模型根据主题自行设计）"

    user_input_parts = [
        f"主题: {scenario}",
        f"玩家角色: {player_role}",
        f"教学目标:",
        goals_text,
        f"目标章节数: {chapter_count}",
        f"目标结局数: {ending_count}",
        f"每章决策点数: {decision_point_count}",
        f"目标游戏时长: {duration_minutes} 分钟",
        f"NPC 数量要求: 必须恰好设计 {npc_count} 个 NPC 角色（不含玩家角色），覆盖不同群体和立场",
    ]
    if character_settings:
        user_input_parts.append(f"人物设定: {character_settings}")
    if story_background:
        user_input_parts.append(f"故事背景: {story_background}")
    if extra_requirements:
        user_input_parts.append(f"额外要求: {extra_requirements}")
    user_input = "\n".join(user_input_parts)

    prompt = f"""你是一位严肃游戏剧本设计师，专精于公共管理题材。
你的任务是生成一个可进入后续制作的剧本制作包全局设定。你现在不写章节剧情，只建立后续写作、规则实现和人工审校都能依赖的基础规格。

请根据以下信息，生成游戏的全局设定 Markdown。

{user_input}

请按以下模板输出。必须使用这些一级和二级标题，不要删减章节。

# 剧本制作包：全局设定

## 项目概览
- 标题:
- 目标游戏时长:
- 玩家角色:
- 核心主题:
- 教学目标:
  - 目标 1:
  - 目标 2:
  - 目标 3:
- 体验关键词:
- 推荐玩家人数与单局节奏:

## 世界观与背景
- 政策背景: （200-300 字，说明政策议题、制度约束和真实治理背景）
- 地域与时代设定:
- 核心冲突: （300-500 字，描述主要矛盾、利益相关方和冲突升级路径）
- 主要压力来源:
  - 上级考核压力:
  - 财政和资源约束:
  - 群众诉求与社会关系:
  - 舆情或外部监督:
- 已知事实与合理虚构边界:

## 玩家角色
- 姓名:
- 职位:
- 背景:
- 初始立场:
- 核心动机:
- 可被考验的弱点:
- 可能形成的治理风格:

## 角色表
必须恰好设计 {npc_count} 个 NPC 角色（不含玩家角色），覆盖不同群体（镇村干部、搬迁村民、上级官员、外部监督者、企业代表等）和不同立场（支持、观望、抵制、中立）。每位 NPC 必须填写：

### NPC [npc_id]：[姓名]
- npc_id:
- 姓名:
- npc_type: cadre / external / villager（三选一）
- 身份与所属群体:
- group:
- 核心诉求:
- 底线:
- 可被说服或激化的条件:
- 掌握的信息:
- 与其他 NPC 的关系:
- 初始态度:
- 可能的态度变化路径:
- 初始信任值（0-100）:
- 初始态度值（0-100）:
- 初始焦虑值（0-100）:
- 参照点:
- 从众阈值（0-100）:
- 初始核心诉求是否满足: 是/否
- 初始是否已签约: 是/否

## 变量与机制表
只能使用以下 8 个变量，不得新增或删除。每个变量必须给出变化尺度和触发阈值。

| 变量名 | 中文含义 | 初始值 | 合理范围 | 变化尺度说明 | 关键阈值 | 触发事件 | 对结局的影响 |
|---|---|---:|---|---|---|---|---|
| signed | 签约户数 | 0 | 0-36 | | | | |
| social_stability | 社会稳定指数 | 70 | 0-100 | | | | |
| political_credit | 政治信用 | 70 | 0-100 | | | | |
| public_trust | 群众信任 | 50 | 0-100 | | | | |
| env_clue | 环评线索 | 0 | 0-100 | | | | |
| media_pressure | 舆情压力 | 30 | 0-100 | | | | |
| budget | 财政预算（万元） | 8000 | 0-10000 | | | | |
| days_left | 剩余天数 | 90 | 90→0 | | | | |

## 章节数量: {chapter_count}

## 每章决策点数量: {decision_point_count}

## 结局类型（共 {ending_count} 个）
对每个结局填写：
- ending_id:（ending_01 ~ ending_{ending_count:02d}）
- 标题:
- 类型: good / neutral / bad
- 一句话描述:
- 核心条件概述:
- 玩家策略画像:
- 教学复盘重点:

## 制作约束
- 关键场景:
- 重要道具或文件:
- UI 表现建议:
- 音效和氛围建议:
- 后续人工编辑应重点检查的问题:

## 风格与基调
（100-200 字）

## 参考文献与案例来源
（列举 3-5 个参考案例或文献）

注意：直接输出完整的 Markdown 文档。不要在末尾添加任何建议、下一步提示或向用户提问。"""

    return _pa_message(prompt)


# ============================================================
# Call 2: 生成章节大纲
# ============================================================

def build_call2_prompt(
    game_settings_md: str,
    decision_point_count: int = 3,
) -> list[ChatMessage]:
    """构建 Call 2（章节大纲）的 Prompt。"""

    second_decision_example = ""
    if decision_point_count >= 2:
        second_decision_example = """  - 决策点 2：标题
    - 选项 A（标签）: ...
    - 选项 B（标签）: ...
    - 选项 C（标签）: ...
"""
    additional_decision_note = ""
    if decision_point_count > 2:
        additional_decision_note = (
            f"  （继续按相同格式写到决策点 {decision_point_count}）\n"
        )

    prompt = f"""你是一位游戏剧情架构师。你的任务是基于全局设定，设计可制作、可审校、可直接交给逐章写作器展开的章节写作包。
章节数量由全局设定决定。每章必须设计恰好 {decision_point_count} 个顺序执行的核心决策点。你不写完整章节正文，但必须把逐章写作所需的局部事实、变量流、flag 流、NPC 变化和制作约束一次性限定好。

请基于以下全局设定，生成章节写作包集合。后续逐章生成时只会把“当前章写作包”发给写作器，不会再附带完整全局设定；因此每章写作包必须自洽，包含该章写作需要遵守的全局事实摘录和禁止改动事项。

{game_settings_md}

请按以下模板输出。必须使用这些标题，不要省略「分支与状态追踪总表」和「制作备注」。

# 剧本制作包：章节写作包集合

## 结局可达性验证

（目的：确保全局设定中定义的每个结局，在本大纲的 flag 结构和变量流向下是可达的。
此处不预设具体的选择序列——那是玩家的事。此处验证的是：存在至少一组选择能到达该结局。）

对全局设定中定义的每个结局，填写：

### 结局 [ending_id]：[标题]（类型: good/neutral/bad）

- 需要的最终变量状态: （例如 env_clue 高, signed 高, political_credit 不低）
- 必须携带的 flag: （例如 flag_evi_has_env_report）
- 必须避免的 flag: （例如 flag_strat_secret_deal）
- 在大纲中如何达成:
  - 哪些章的选项可以累积所需变量？（列出章号和选项标签）
  - 哪些章的选项会设置必须 flag？（列出章号和选项标签）
  - 哪些章的选项会设置禁止 flag？（必须被避免的选择）
- 是否存在矛盾？（例如：要同时拿到 flag_X 和 flag_Y，但它们在互斥的选项中）如有矛盾，调整大纲。

如果某个结局在当前大纲下不可达，此节必须明确指出，并建议修改具体章节的 decision_framework 以打开路径。

## 第 1 章：标题
- chapter_id: ch01
- day_range: 第X-Y天（正序，X < Y，表示游戏的第X天到第Y天）
- core_task:
- main_question:
- learning_goals:
- global_facts_for_this_chapter:
  - 本章必须遵守的 NPC 身份、变量含义、世界观事实、结局约束或关键道具事实
  - 本章禁止改动或新增的事实
- entry_state_assumptions:
  - 从前序章节承接的变量范围、已知信息、已激活 flag、NPC 初始态度
- exit_state_contract:
  - 本章结束时必须交付给后续章节的变量范围、flag、解锁节点、未解决线索
- scene_brief: （本章主要场景、关键人物、需要呈现的氛围）
- required_npcs:
  - npc_id / 姓名 / 本章立场 / 本章目标 / 本章可能变化
- info_nodes:
  - node_id:
  - 玩家获得的信息:
  - 解锁条件:
- decision_framework:
  （本章必须设计恰好 {decision_point_count} 个顺序决策点，第 N 个决策点的结果自然导向第 N+1 个决策点）
  - 决策点 1：标题
    - 选项 A（标签）: 核心逻辑 → 主要变量影响方向 → NPC 状态变化 → 设置的 flag → 解锁/关闭内容 → 教学反馈重点
    - 选项 B（标签）: ...
    - 选项 C（标签）: ...
    - 选项 D（标签）: ...
{second_decision_example}{additional_decision_note}- variables_in_focus: [本章重点关注的变量]
- flag_design: [本章可能设置的 flag 及设计意图]
- npc_state_plan: [本章关键 NPC 的态度、信任、焦虑或信息状态如何变化]
- continuity_contract: [本章必须承接前章什么事实，并向后章交付什么事实]
- production_notes: [本章前端、美术、音效或交互制作要点]

## 第 2 章：标题
（同上格式）

...（共 N 章，N = 全局设定中定义的章节数量）

## Flag 全局规划表

| flag_id | 创建于 | 作用（解锁什么 / 关闭什么） | 参与哪个结局 |
|---|---|---|---|
| flag_xxx | ch0X_A | 解锁 ch0Y_info_Z | ending_01 |
| ... | ... | ... | ... |

## 分支与状态追踪总表

| choice_id | 所属章节 | 变量变化方向 | NPC 状态变化 | 新增/移除 flag | 解锁内容 | 关闭内容 | 长期影响 | 关联结局 |
|---|---|---|---|---|---|---|---|---|
| ch01_1A | ch01 | signed + / public_trust - | npc_x trust - | flag_x | ch02_info_x | ch03_choice_y | 后续谈判更难 | neutral/bad |

## 制作备注
- 关键场景清单:
- 重要道具或文件:
- UI 表现建议:
- 音效和氛围建议:
- 仍需人工补充的问题:

注意：直接输出完整的 Markdown 文档。不要在末尾添加任何建议、下一步提示或向用户提问。"""

    return _pa_message(prompt)


# ============================================================
# Call 3: 逐章生成 Markdown 剧本
# ============================================================

def _extract_outline_entry(outline_md: str, chapter_num: int) -> str:
    pattern = rf'(## 第 {chapter_num} 章[^\n]*\n.*?)(?=\n## 第 {chapter_num + 1} 章|\n## Flag 全局规划|\n## 结局可达|\Z)'
    match = re.search(pattern, outline_md, re.DOTALL)
    if match:
        return match.group(1).strip()
    pattern2 = rf'(## 第{chapter_num}章[^\n]*\n.*?)(?=\n## 第{chapter_num + 1}章|\n## Flag 全局|\n## 结局可达|\Z)'
    match2 = re.search(pattern2, outline_md, re.DOTALL)
    return match2.group(1).strip() if match2 else ""


def _chapter_writing_package_for_call3(
    chapter_outline_md: str,
    current_chapter_num: int,
    current_chapter_outline_entry: str,
) -> str:
    package = current_chapter_outline_entry.strip()
    if not package:
        package = _extract_outline_entry(chapter_outline_md, current_chapter_num)
    return package.strip() or f"## 第 {current_chapter_num} 章\n（章节写作包缺失，请基于前序状态补全。）"


def _chapter_structure_contract(decision_point_count: int) -> str:
    return f"""# 第X章：章节标题

## chapter_info
- chapter_id / title / day_range / decision_point_count: {decision_point_count} / entry_conditions

## 背景情境
写 300-500 字，包含明线压力、暗线线索、关键 NPC 动机、玩家当前约束。

## 信息节点
### 信息节点 1：标题
- node_id / visible_if / content / reveals / related_npcs / unlocks:
（按需要继续写信息节点。）

## 决策点
### 决策点 1：决策标题
- decision_id: chXX_dp01
- order: 1
- situation:
- visible_if:
- options:
  - option_id: A
    label / action_cost / narrative:
    effects: signed, social_stability, political_credit, public_trust, env_clue, media_pressure, budget, days_left 全部列出，无变化写 0
    npc_state_changes: 覆盖 trust、attitude、anxiety、known_info 或 stance
    flags_opened / flags_closed / unlocks / closes / next / feedback:
  （每个决策点写 3-5 个选项；按相同结构写到决策点 {decision_point_count}；最后一个 next 指向 checkpoint。）

## 分支结果
### 决策点 1 的结果
- A:
  - immediate_result / delayed_result / npc_reactions / variable_logic:
（继续写完所有决策点的结果。）

## 章节结算
- checkpoint_id / summary / variable_delta_summary / npc_state_summary / flags_opened / flags_closed / next_chapter:

## 状态快照
- variables / npc_states / open_flags / closed_flags / unresolved_threads:

## 章节总结
写清本章完成了什么、留下什么风险、如何牵引下一章。

## 制作备注
写 UI、场景背景图、音效、交互页面、需要人工确认的问题。"""

def build_call3_prompt(
    game_settings_md: str,
    chapter_outline_md: str,
    current_chapter_num: int,
    total_chapters: int,
    current_chapter_outline_entry: str,
    state_snapshot_text: str,
    unlocked_nodes_text: str,
    locked_nodes_text: str,
    chapter_template_md: str,
    few_shot_example_md: str,
    previous_chapters_summary: str = "",
    decision_point_count: int = 3,
) -> list[ChatMessage]:
    """构建 Call 3（逐章生成）的 Prompt。

    Args:
        game_settings_md: 兼容旧签名，Call 3 不再直接发送完整全局设定
        chapter_outline_md: 兼容旧签名，仅在当前章写作包缺失时用于抽取当前章
        current_chapter_num: 当前章节编号（1-based）
        total_chapters: 总章节数
        current_chapter_outline_entry: 当前章写作包
        state_snapshot_text: 前序状态快照的文本表示
        unlocked_nodes_text: 已解锁节点清单文本
        locked_nodes_text: 已关闭节点清单文本
        chapter_template_md: 兼容旧签名，Call 3 使用内置结构契约
        few_shot_example_md: 兼容旧签名，Call 3 不再发送 few-shot
        previous_chapters_summary: 前序章节摘要（用于控制上下文长度）
    """

    chapter_writing_package = _chapter_writing_package_for_call3(
        chapter_outline_md,
        current_chapter_num,
        current_chapter_outline_entry,
    )
    structure_contract = _chapter_structure_contract(decision_point_count)
    is_first = current_chapter_num == 1
    is_last = current_chapter_num == total_chapters

    chapter_position_note = ""
    if is_first:
        chapter_position_note = "这是第一章。背景情境从初始状态出发，不需要条件文本段。"
    elif is_last:
        chapter_position_note = (
            "这是最后一章。章节结算的 next_chapter 写 ending_evaluation。"
            "在决策选项中需要特别关注结局条件的累积。"
        )

    previous_context = ""
    if previous_chapters_summary:
        previous_context = f"""
## 前序章节摘要
{previous_chapters_summary}
"""
    else:
        previous_context = """
## 前序章节摘要
这是第一章，无前序章节。
"""

    prompt = f"""你是一位严肃游戏剧本作家。请生成第 {current_chapter_num} 章的剧本 Markdown 文本。

核心原则：
1. 严格遵循模板，输出可交付给制作团队的章节制作包。
2. 本章必须恰好 {decision_point_count} 个顺序决策点，chapter_info 中写 decision_point_count: {decision_point_count}。
3. 每个决策点 3-5 个选项；每个选项必须写 signed, social_stability, political_credit, public_trust, env_clue, media_pressure, budget, days_left 的影响、NPC 状态变化、flag、解锁/关闭节点、next 和教学反馈。
4. 决策点按 order 顺序承接，最后一个决策点的结果 next 指向 checkpoint。
5. 背景情境控制在 300-500 字，必要时用 [若 flag]...[/若] 或 [若 变量>=N]...[/若] 表达条件状态。
6. 选项之间要有真实治理张力；不存在所有变量同时上升的神选项。
7. day_range 用正序“第X-Y天”（X < Y）。若大纲写 days_left 从74降到45，则写第16-45天。
8. 不确定处自行合理假设，不要 request_clarification，并在「制作备注」标记人工确认项。
{chapter_position_note}

## 当前章写作包
{chapter_writing_package}

{previous_context}
## 前序状态快照
{state_snapshot_text}

此快照记录了到达本章时玩家可能的状态范围。请在「背景情境」中据此编写条件文本段。

## 已解锁的后续节点
{unlocked_nodes_text}

## 已关闭的后续节点
{locked_nodes_text}

## Markdown 模板（必须严格遵循）
{structure_contract}

注意：直接输出完整的 Markdown 文档。不要在末尾添加任何建议、下一步提示或向用户提问。"""

    return _pa_message(prompt)


# ============================================================
# Call 4: 事实连续性审查
# ============================================================

def build_call4_prompt(complete_script_md: str, user_feedback: str = "") -> list[ChatMessage]:
    """构建 Call 4（事实连续性审查）的 Prompt。

    Args:
        complete_script_md: 完整的剧本 Markdown（所有章节合并）
        user_feedback: 可选，用户的全局修订反馈（用于 L4 修订场景）
    """

    feedback_section = ""
    if user_feedback.strip():
        feedback_section = f"""
## 用户修订反馈
{user_feedback.strip()}

将反馈中涉及的明确事实作为额外核对项，但不要据此评价叙事质量。
"""

    system = "你是剧本事实连续性检查器。只查找有两处原文证据直接冲突的问题，不评价策略、教学、人物塑造或叙事质量。只输出 JSON。"

    user = f"""请检查以下完整剧本的事实连续性。

只检查：
1. 同一 NPC 的身份、所属群体或既定事实前后直接矛盾
2. 章节时间顺序、事件发生顺序前后直接矛盾
3. 前章明确发生的结果与后章明确描述的状态直接矛盾
4. 同一客观数值被文本声明为两个不能同时成立的最终值

不要检查：
- 策略张力、教学反馈、行为是否合理、人物塑造、文学表现、叙事质量
- JSON 结构、节点、flag、变量字段、结局可达性；这些由程序校验
- 只有推测、偏好或解释空间的问题

每个问题必须提供两处互相矛盾的原文证据。缺少两处证据就不要报告。
引用原文时单处不超过 100 字，不要输出完整剧本。

{feedback_section}
## 完整剧本
{complete_script_md}

## 输出格式

输出合法 JSON：
{{
  "status": "pass | review_recommended",
  "continuity_issues": [
    {{
      "code": "CONTINUITY_001",
      "message": "明确的事实矛盾",
      "source_a": {{"location": "章节/节点", "quote": "原文证据"}},
      "source_b": {{"location": "章节/节点", "quote": "冲突原文证据"}}
    }}
  ]
}}

没有明确矛盾时输出 status=pass 和空数组。只输出 JSON。"""

    return _flash_messages(system, user)


def build_call4_repair_prompt(
    sources: dict[str, str],
    previous_issues: list[dict] | None = None,
) -> list[ChatMessage]:
    """Build a strict continuity review prompt that returns safe text patches."""
    source_text = "\n\n".join(
        f"===== FILE: {filename} =====\n{content}"
        for filename, content in sources.items()
    )
    previous_text = ""
    if previous_issues:
        previous_text = (
            "\n\n上轮仍需复查的问题：\n"
            + json.dumps(previous_issues, ensure_ascii=False, indent=2)
        )
    system = (
        "你是剧本事实连续性修复器。只处理有两处原文证据直接冲突的问题，"
        "并给出最小范围的精确文本替换。不得评价或重写策略、教学、人物塑造和叙事质量。"
        "只输出 JSON。"
    )
    user = f"""检查以下剧本源 Markdown 的事实连续性，并为可安全修复的问题提供补丁。

只检查：
1. 同一 NPC 的身份、所属群体或既定事实前后直接矛盾
2. 章节时间、事件发生顺序前后直接矛盾
3. 前章明确结果与后章明确状态直接矛盾
4. 同一客观数值存在不能同时成立的最终值

修复优先级：全局设定高于章节大纲，章节大纲高于章节正文；章节之间以前章明确发生的结果为准。
每个补丁的 old_text 必须逐字复制目标文件中一段能够唯一匹配的原文，new_text 只做解决矛盾所需的最小修改。
无法确定正确事实、无法唯一定位或缺少双重证据时，只报告问题，不提供补丁。
{previous_text}

## 源文件
{source_text}

## 输出格式
{{
  "status": "pass | repair_required",
  "continuity_issues": [
    {{
      "code": "CONTINUITY_001",
      "message": "明确的事实矛盾",
      "source_a": {{"file": "03_ch01.md", "quote": "原文证据"}},
      "source_b": {{"file": "03_ch02.md", "quote": "冲突原文证据"}}
    }}
  ],
  "patches": [
    {{
      "issue_code": "CONTINUITY_001",
      "file": "03_ch02.md",
      "old_text": "目标文件中唯一出现的原文",
      "new_text": "修复后的最小替换文本",
      "reason": "为什么选择修改这一处"
    }}
  ]
}}

没有明确矛盾时输出 status=pass、空 continuity_issues 和空 patches。只输出 JSON。"""
    return _flash_messages(system, user)


# ============================================================
# Call 5: JSON 抽取（分段：5a 全局 + 5b 逐章）
# ============================================================

_CALL5_SYSTEM = "你是一个结构化数据提取器。你只负责从 Markdown 中提取已存在的信息，不创作、不新增、不修改任何内容。缺失字段填 null 或空数组。输出纯 JSON。"

_VARIABLE_SCHEMA = '''{
    "signed": {"chinese_name": "签约户数", "range": "0-36"},
    "social_stability": {"chinese_name": "社会稳定指数", "range": "0-100"},
    "political_credit": {"chinese_name": "政治信用", "range": "0-100"},
    "public_trust": {"chinese_name": "群众信任", "range": "0-100"},
    "env_clue": {"chinese_name": "环境线索", "range": "0-100"},
    "media_pressure": {"chinese_name": "舆论压力", "range": "0-100"},
    "budget": {"chinese_name": "剩余预算（万元）", "range": "0-20000"},
    "days_left": {"chinese_name": "剩余天数", "range": "0-90"}
  }'''

_NPC_SCHEMA = '''"npc_id": "string",
      "name": "string",
      "npc_type": "cadre | external | villager",
      "group": "string",
      "core_demand": "string",
      "bottom_line": "string",
      "persuasion_conditions": ["string"],
      "known_info": ["string"],
      "relationships": ["string"],
      "initial_stance": "string",
      "attitude_path": "string",
      "trust_to_player": "number",
      "attitude_score": "number",
      "anxiety_level": "number",
      "reference_point": "number",
      "granovetter_threshold": "number",
      "core_demand_satisfied": "boolean",
      "signed": "boolean"'''


def build_call5a_prompt(complete_script_md: str, chapter_ids: list[str]) -> list[ChatMessage]:
    """构建 Call 5a（全局结构 + NPC + 结局 抽取）的 Prompt。

    只抽全局结构、变量、初始状态、NPC 表和结局，不抽各章剧情树。
    """

    chapters_list = "\n".join(f"  - {cid}" for cid in chapter_ids)

    user = f"""请从以下 Markdown 剧本中提取**全局结构**、**NPC 角色表** 和 **多结局**。

## 完整剧本
{complete_script_md}

## 输出 JSON Schema（只输出以下字段，不包含 chapters）

{{
  "title": "string",
  "player_role": "string",
  "core_conflict": "string",
  "variables": [
    {{"name": "string", "chinese_name": "string", "initial_value": "number", "range": "string"}}
  ],
  "initial_state": {{
    "signed": 0,
    "social_stability": 70,
    "political_credit": 70,
    "public_trust": 50,
    "env_clue": 0,
    "media_pressure": 30,
    "budget": 8000,
    "days_left": 90
  }},
  "npcs": [
    {{
      {_NPC_SCHEMA}
    }}
  ],
  "endings": [
    {{
      "ending_id": "string",
      "title": "string",
      "type": "good | neutral | bad",
      "description": "string",
      "conditions": {{
        "variables": {{"budget": ">= 1000"}},
        "variable_expression": "budget >= 1000 AND signed >= 30",
        "flags_required_all": ["string"],
        "flags_required_any": ["string"],
        "flags_forbidden": ["string"],
        "npc_states": {{}}
      }},
      "ending_text": "string",
      "teaching_summary": "string",
      "strategy_profile": "string"
    }}
  ],
  "_chapter_ids": [{", ".join(f'"{cid}"' for cid in chapter_ids)}]
}}

## 抽取规则
1. 只抽取 Markdown 中明确存在的内容；缺失字段填 null（对象）或 []（数组）或 0（数字）
2. 8 个变量必须全部提取：signed, social_stability, political_credit, public_trust, env_clue, media_pressure, budget, days_left
3. 必须从「角色表」提取全部 NPC 到 npcs，每个字段都不能遗漏
4. 必须从大纲和全文提取全部结局到 endings（通常 {len(chapter_ids)*2} 个左右）
5. 结局 conditions.variables 的每个值必须是保留比较符的字符串，例如 ">= 1000"、"< 30"；禁止把 "budget >= 1000" 抽取成裸数字 1000
6. variable_expression 必须保留变量条件之间原始的 AND、OR 和括号；flags_required_all 表示全部必须具备，flags_required_any 表示任意具备一个即可，禁止把“或”抽成“且”
7. 输出合法 JSON，不包含 markdown 代码块标记
8. 直接输出纯 JSON，不要在 JSON 前后添加任何说明文字"""

    return _flash_messages(_CALL5_SYSTEM, user)


def build_call5b_prompt(chapter_md: str, chapter_id: str, chapter_num: int) -> list[ChatMessage]:
    """构建 Call 5b（单章剧情树 抽取）的 Prompt。

    只抽指定章节的 info_nodes、decision_points、results、checkpoint。
    """

    user = f"""请从以下单章 Markdown 中提取该章的剧情树 JSON。

## 第 {chapter_num} 章 Markdown
{chapter_md}

## 输出 JSON Schema（只输出单章字段）

{{
  "chapter_id": "{chapter_id}",
  "title": "string",
  "day_range": "string",
  "core_task": "string",
  "main_question": "string",
  "learning_goals": ["string"],
  "background": "string",
  "info_nodes": [
    {{
      "node_id": "string",
      "title": "string",
      "content": "string",
      "unlock_condition": null,
      "next": "string"
    }}
  ],
  "decision_points": [
    {{
      "node_id": "string",
      "question": "string",
      "order": "number",
      "options": [
        {{
          "choice_id": "string",
          "option_label": "string",
          "text": "string",
          "availability": null,
          "effects": {{
            "signed": "number",
            "social_stability": "number",
            "political_credit": "number",
            "public_trust": "number",
            "env_clue": "number",
            "media_pressure": "number",
            "budget": "number",
            "days_left": "number"
          }},
          "npc_state_changes": {{}},
          "unlock_nodes": ["string"],
          "lock_nodes": ["string"],
          "flags_added": ["string"],
          "flags_removed": ["string"],
          "immediate_result_text": "string",
          "long_term_effect": "string",
          "teaching_feedback": "string"
        }}
      ]
    }}
  ],
  "results": [
    {{
      "node_id": "string",
      "from_choice": "string",
      "text": "string",
      "next": "string"
    }}
  ],
  "checkpoint": {{
    "checkpoint_id": "string",
    "merge_from": ["string"],
    "next_chapter": "string",
    "variable_snapshot": {{
      "signed": "number",
      "social_stability": "number",
      "political_credit": "number",
      "public_trust": "number",
      "env_clue": "number",
      "media_pressure": "number",
      "budget": "number",
      "days_left": "number"
    }},
    "active_flags": ["string"],
    "unlocked_nodes": ["string"],
    "locked_nodes": ["string"],
    "summary": "string"
  }}
}}

## 抽取规则
1. 只抽取该章 Markdown 中明确存在的内容；缺失字段填 null 或 [] 或 0
2. 8 个变量的 effects 必须全部列出，即使值为 0
3. 条件文本段（[若...]...[/若]）在 background 和 text 中保留原样
4. checkpoint 的 variable_snapshot 从「状态快照」节提取最可能值（第3列）
5. 输出合法 JSON，不包含 markdown 代码块标记
6. 直接输出纯 JSON，不要在 JSON 前后添加任何说明文字"""

    return _flash_messages(_CALL5_SYSTEM, user)


def build_call5_prompt(complete_script_md: str) -> list[ChatMessage]:
    """兼容旧接口：构建 Call 5（JSON 抽取）的 Prompt（单调用版，大剧本可能截断）。"""
    from src.generation.chapter_prompts import build_call5a_prompt, build_call5b_prompt
    # 简单回退：用 Call 5a 作为默认
    return build_call5a_prompt(complete_script_md, ["ch01", "ch02", "ch03", "ch04"])


# ============================================================
# Markdown 模板
# ============================================================

CHAPTER_TEMPLATE_MD = """# 第X章：章节标题

## 章节信息
- chapter_id: ch0X
- day_range: 第X-Y天（正序，X < Y。X = 章节开始时已过天数，Y = 章节结束时已过天数。消耗 = Y-X 天）
- core_task: 本章核心任务（一句话）
- main_question: 本章核心决策问题（以问号结尾）
- decision_point_count: N
  # N 必须等于生成请求指定的数量。决策点顺序执行，前一决策的结果影响后续决策的情境。
- unlock_condition: null
  # 如果本章本身需要前置条件才能进入，在此写明
  # 格式：{flags_required: [...], flags_forbidden: [...], variables: {...}}
- learning_goals:
  - 教学目标 1
  - 教学目标 2

## 背景情境

（300-500 字，描述玩家进入本章时面对的局面。）

**写作要求：**
- 必须反映前序章节的遗留问题
- 使用 `[若 flag_xxx]...[/若]` 标记条件文本段，覆盖不同到达状态
- 使用 `[若 variable >= N]...[/若]` 标记变量条件文本段
- 为没有携带特定 flag 的路径提供默认文本

## 信息节点

（2-3 个信息节点，提供决策所需的背景信息。部分节点可设置解锁条件。）

### 信息节点 1：节点标题

- node_id: ch0X_info_01
- node_type: INFO
- unlock_condition: null
  # 格式：{flags_required: [...], flags_forbidden: [...], variables: {...}}
- next: ch0X_choice_1

（150-300 字。玩家可获得的信息——访谈内容、材料发现、现场观察、数据报表等。）

### 信息节点 2：节点标题

- node_id: ch0X_info_02
- node_type: INFO
- unlock_condition: {flags_required: [flag_example]}
  # 仅在前序章节获得了特定 flag 时才出现此信息节点
- next: ch0X_choice_1

（如果没有解锁，此节点在游戏中不出现。）

## 核心决策点

（本章包含生成请求指定数量的顺序决策点。玩家依次面对，前一个决策的即时后果将作为下一个决策的情境铺垫。每个决策点必须有 3-5 个选项，不能只有 A/B 两个选项。）

### 决策点 1：决策标题

- node_id: ch0X_choice_1
- node_type: CHOICE
- order: 1
- question: 玩家需要做出的选择问题（以问号结尾，清晰、有张力）

#### 选项 A：选项标题

- choice_id: ch0X_1A
- option_label: A
- 选项文本: （玩家在选择界面看到的一句话描述，20-40 字）
- 可用条件: null
  # 格式：{variables: {political_credit: ">= 30"}, flags_required: [...], flags_forbidden: [...]}
  # null 表示始终可选
- 变量影响:
  - signed: +3
  - social_stability: -5
  - political_credit: +5
  - public_trust: -5
  - env_clue: +15
  - media_pressure: +5
  - budget: -200
  - days_left: -7
  # 每个选项必须列出所有 8 个变量，无变化写 0
- NPC 状态变化:
  - npc_id: npc_xxx
    trust: -5
    attitude: -10
    anxiety: +10
    known_info: ["新增或改变的信息"]
    stance: "立场变化说明"
  # 至少列出直接受影响 NPC；没有变化写 []
- 解锁节点: [ch03_info_secret_report, ch04_choice_legal_path]
  # 后续章节中因此选项而解锁的节点 ID 列表，无则写 []
- 关闭节点: [ch05_choice_backroom_deal]
  # 后续章节中因此选项而被关闭的节点 ID 列表，无则写 []
- 新增 flag: [flag_evi_has_env_data, flag_stance_legal_approach]
  # 无则写 []
- 移除 flag: []
  # 无则写 []
- 即时后果: （150-250 字，玩家选择后立即看到的叙事结果）
- 长期影响: （50-100 字，对此后章节走向的预期影响）
- 教学反馈: （100-150 字，从公共管理视角点评此选择，点明治理逻辑和代价）

#### 选项 B：选项标题

（同上结构，choice_id 为 ch0X_1B）

#### 选项 C：选项标题

（同上结构，choice_id 为 ch0X_1C。选项 C 必须是独立策略路径，不得只是 A 或 B 的同义改写。）

#### 选项 D：选项标题

（同上结构，choice_id 为 ch0X_1D。如只有 3 个选项则不写 D）

### 决策点 2：决策标题

- node_id: ch0X_choice_2
- node_type: CHOICE
- order: 2
- question: 基于前一决策的结果，玩家现在需要做出的选择问题
- prerequisite: 承接自决策点 1 的结果（玩家已看过 DP1 的即时后果文本）

#### 选项 A：选项标题

- choice_id: ch0X_2A
- option_label: A
- 选项文本: ...
- 可用条件: null
  # 可根据 DP1 的选择结果设置条件，例如 {flags_required: [flag_ch0X_dp1_chose_A]}
- 变量影响:
  - signed: 0
  - social_stability: 0
  - political_credit: 0
  - public_trust: 0
  - env_clue: 0
  - media_pressure: 0
  - budget: 0
  - days_left: 0
  # 每个选项必须列出所有 8 个变量，无变化写 0
- NPC 状态变化: []
- 解锁节点: []
- 关闭节点: []
- 新增 flag: []
- 移除 flag: []
- 即时后果: ...
- 长期影响: ...
- 教学反馈: ...

#### 选项 B / C / D

（同上结构。即使是后续承接型决策点，也必须至少写 A/B/C 三个选项；不得只写 A/B。）

（按生成请求指定的数量继续追加决策点。最后一个决策点的结果 next 指向 checkpoint。）

## 分支结果

（按决策点分组。每个决策点的每个选项对应一个结果节点。）

### 决策点 1 的结果

#### 结果 1A：结果标题

- node_id: ch0X_result_1A
- node_type: RESULT
- from_choice: ch0X_1A
- next: ch0X_choice_2
  # 指向下一决策点；若是最后一个 DP 的结果，则指向 ch0X_checkpoint

（200-400 字，玩家选择后看到的完整叙事结果。应包含具体场景、NPC 反应、即时变化。）

#### 结果 1B / 1C / 1D

（同上，next 均指向 ch0X_choice_2）

### 决策点 2 的结果

#### 结果 2A：结果标题

- node_id: ch0X_result_2A
- node_type: RESULT
- from_choice: ch0X_2A
- next: ch0X_checkpoint
  # 最后一个决策点的结果均指向章节结算

#### 结果 2B / 2C / 2D

（同上，next 均指向 ch0X_checkpoint）

## 章节结算

- checkpoint_id: ch0X_checkpoint
- node_type: CHECKPOINT
- merge_from:
  - ch0X_result_2A
  - ch0X_result_2B
  - ch0X_result_2C
  - ch0X_result_2D
  # 列出最后一个决策点的所有结果节点 ID（所有路径最终汇流于此）
- next_chapter: ch0Y
  # 下一章的 chapter_id；如果是最后一章，写 ending_evaluation

## 状态快照

**这是跨章节状态传递的关键数据结构。每章结束时必须填写，作为下一章生成的输入。**

### 变量范围（考虑本章所有可能路径——所有 DP 选项组合的累积效果）
| 变量 | 最低值 | 最高值 | 最可能路径 |
|---|---|---|---|
| signed | X | Y | Z（选项组合 X） |
| social_stability | X | Y | Z |
| political_credit | X | Y | Z |
| public_trust | X | Y | Z |
| env_clue | X | Y | Z |
| media_pressure | X | Y | Z |
| budget | X | Y | Z |
| days_left | X | Y | Z |

### 可能激活的 Flag 集合
（玩家到达本章结算时可能携带的所有 flag，标注来源选项。覆盖所有 DP。）
- flag_xxx: 来自 ch0X_1A（始终激活）
- flag_yyy: 来自 ch0X_2C（始终激活）
- flag_zzz: 来自 ch01_B（前序章节，始终激活）

### 已解锁的后续节点
（综合所有路径，后续章节中哪些节点已被解锁）
- ch03_info_secret_report（需 flag_xxx）
- ch04_choice_legal_path（需 flag_xxx）

### 已关闭的后续节点
（综合所有路径，后续章节中哪些节点已被关闭）
- ch05_choice_backroom_deal（被 flag_xxx 关闭）

### 章节总结
（100-200 字，总结本章的关键事件和状态变化，为下一章的背景情境提供衔接。）

## 制作备注
- 关键场景:
- 重要道具或文件:
- UI 表现建议:
- 音效和氛围建议:
- 需要人工补充或确认的问题:"""


# ============================================================
# 状态快照文本构建
# ============================================================

def build_state_snapshot_text(snapshot: "ChapterStateSnapshot") -> str:
    """将 StateSnapshot 转换为可嵌入 Prompt 的文本表示。"""

    var_lines = []
    for var_name in VARIABLE_NAMES:
        chinese = _var_chinese_name(var_name)
        val = getattr(snapshot, var_name, 0)
        ranges = snapshot.variable_ranges.get(var_name, {})
        min_v = ranges.get("min", val)
        max_v = ranges.get("max", val)
        most = ranges.get("most_likely", val)
        var_lines.append(f"  - {var_name}（{chinese}）: 当前 {val}，范围 [{min_v}, {max_v}]，最可能 {most}")

    flags_text = "\n".join(f"  - {f}" for f in sorted(snapshot.active_flags)) if snapshot.active_flags else "  - （无）"
    unlocked_text = "\n".join(f"  - {n}" for n in sorted(snapshot.unlocked_nodes)) if snapshot.unlocked_nodes else "  - （无）"
    locked_text = "\n".join(f"  - {n}" for n in sorted(snapshot.locked_nodes)) if snapshot.locked_nodes else "  - （无）"

    return f"""### 当前变量值
{'\n'.join(var_lines)}

### 已激活的 Flag
{flags_text}

### 已解锁的后续节点
{unlocked_text}

### 已关闭的后续节点
{locked_text}"""


def build_initial_state_snapshot_text() -> str:
    """构建初始状态快照文本（用于第 1 章）。"""
    from src.domain.chapter_structure import ChapterStateSnapshot
    snapshot = ChapterStateSnapshot(
        signed=0, social_stability=70, political_credit=70,
        public_trust=50, env_clue=0, media_pressure=30,
        budget=8000, days_left=90,
        active_flags=set(), unlocked_nodes=set(), locked_nodes=set(),
        variable_ranges={
            "signed": {"min": 0, "max": 0, "most_likely": 0},
            "social_stability": {"min": 70, "max": 70, "most_likely": 70},
            "political_credit": {"min": 70, "max": 70, "most_likely": 70},
            "public_trust": {"min": 50, "max": 50, "most_likely": 50},
            "env_clue": {"min": 0, "max": 0, "most_likely": 0},
            "media_pressure": {"min": 30, "max": 30, "most_likely": 30},
            "budget": {"min": 8000, "max": 8000, "most_likely": 8000},
            "days_left": {"min": 90, "max": 90, "most_likely": 90},
        },
    )
    return build_state_snapshot_text(snapshot)


def build_unlocked_nodes_text(snapshot: "ChapterStateSnapshot") -> str:
    """从 StateSnapshot 构建已解锁节点清单文本。"""
    if not snapshot.unlocked_nodes:
        return "（暂无已解锁的后续节点）"
    return "\n".join(f"- {n}" for n in sorted(snapshot.unlocked_nodes))


def build_locked_nodes_text(snapshot: "ChapterStateSnapshot") -> str:
    """从 StateSnapshot 构建已关闭节点清单文本。"""
    if not snapshot.locked_nodes:
        return "（暂无已关闭的后续节点）"
    return "\n".join(f"- {n}" for n in sorted(snapshot.locked_nodes))


def _var_chinese_name(var_name: str) -> str:
    """获取变量的中文名称。"""
    names = {
        "signed": "签约户数",
        "social_stability": "社会稳定指数",
        "political_credit": "政治信用",
        "public_trust": "群众信任",
        "env_clue": "环评线索",
        "media_pressure": "舆情压力",
        "budget": "财政预算",
        "days_left": "剩余天数",
    }
    return names.get(var_name, var_name)
