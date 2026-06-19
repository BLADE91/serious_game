"""6-Call 章节式剧本生成的 Prompt 构建函数。

Call 1-3 使用 PA Backend（创作），Call 4-6 使用 Qwen Flash（审校与抽取）。
只有 Call 1-3 的 Prompt 末尾需要「注意：不要给建议」指令，
因为 PA Backend 的 LLM 倾向于在输出末尾给出下一步建议或向用户提问。
"""

from __future__ import annotations

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
- 身份与所属群体:
- 核心诉求:
- 底线:
- 可被说服或激化的条件:
- 掌握的信息:
- 与其他 NPC 的关系:
- 初始态度:
- 可能的态度变化路径:

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

def build_call2_prompt(game_settings_md: str) -> list[ChatMessage]:
    """构建 Call 2（章节大纲）的 Prompt。"""

    prompt = f"""你是一位游戏剧情架构师。你的任务是基于全局设定，设计可制作、可审校的章节规划。
章节数量由全局设定决定。每章设计 2-4 个顺序执行的核心决策点。你不写详细剧情，只搭骨架、变量流、flag 流和制作约束。

请基于以下全局设定，生成章节大纲。

{game_settings_md}

请按以下模板输出。必须使用这些标题，不要省略「分支与状态追踪总表」和「制作备注」。

# 剧本制作包：章节规划

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
- scene_brief: （本章主要场景、关键人物、需要呈现的氛围）
- info_nodes:
  - node_id:
  - 玩家获得的信息:
  - 解锁条件:
- decision_framework:
  （每章设计 2-4 个顺序决策点，第 N 个决策点的结果自然导向第 N+1 个决策点）
  - 决策点 1：标题
    - 选项 A（标签）: 核心逻辑 → 主要变量影响方向 → NPC 状态变化 → 设置的 flag → 解锁/关闭内容 → 教学反馈重点
    - 选项 B（标签）: ...
    - 选项 C（标签）: ...
    - 选项 D（标签）: ...
  - 决策点 2：标题
    - 选项 A（标签）: ...
    - 选项 B（标签）: ...
    - 选项 C（标签）: ...
- variables_in_focus: [本章重点关注的变量]
- flag_design: [本章可能设置的 flag 及设计意图]
- npc_state_plan: [本章关键 NPC 的态度、信任、焦虑或信息状态如何变化]
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
) -> list[ChatMessage]:
    """构建 Call 3（逐章生成）的 Prompt。

    Args:
        game_settings_md: 全局设定全文
        chapter_outline_md: 章节大纲全文
        current_chapter_num: 当前章节编号（1-based）
        total_chapters: 总章节数
        current_chapter_outline_entry: 当前章在大纲中的条目
        state_snapshot_text: 前序状态快照的文本表示
        unlocked_nodes_text: 已解锁节点清单文本
        locked_nodes_text: 已关闭节点清单文本
        chapter_template_md: 第 5 节的 Markdown 模板
        few_shot_example_md: Few-shot 示例章节
        previous_chapters_summary: 前序章节摘要（用于控制上下文长度）
    """

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
1. 严格遵循模板格式。模板中的每一个字段都必须填写；输出必须像可交付给制作团队的章节制作包，而不是松散小说。
2. 每章设计 2-4 个顺序决策点（具体数量在 chapter_info 的 decision_point_count 中指定）。
3. 决策点按 order=1, 2, ... 顺序执行；前一个决策的即时后果作为后一个决策的情境铺垫。
4. 每个决策点必须提供 3-5 个选项。只写 A/B 两个选项视为结构失败，必须补足第三种策略路径。
5. 如果冲突看似只有二元选择，第三个选项应是有真实代价的折中、程序化、延迟处理、试点验证、公开协商或第三方介入方案，而不是换一种说法重复 A/B。
6. 每个选项必须有明确的变量影响（8 个变量全部列出，无变化写 0）、flag 变化、解锁/关闭节点。
7. 每个选项必须写出 NPC 状态变化，至少覆盖受影响 NPC 的 trust、attitude、anxiety、known_info 或 stance 变化。
8. 章内所有分支必须汇流到章节结算（最后一个决策点的结果 next 指向 checkpoint）。
9. 在背景情境中使用 [若 flag]...[/若] 和 [若 变量>=N]...[/若] 反映不同到达状态。
10. 叙事要有文学质感，但不能写成纯小说。背景情境控制在 300-500 字。
11. 决策选项之间必须存在真实的伦理或策略张力——没有显然正确的选项。
12. 每个选项的教学反馈必须从公共管理视角点明治理逻辑和代价。
13. 变量变化必须有得有失——不存在所有变量同时上升的神选项。
14. 不确定的地方做出合理假设，不要 request_clarification，并在「制作备注」标记仍需人工确认的问题。
15. day_range 写"第X-Y天"时使用正序（X < Y），表示章节覆盖的游戏第X天到第Y天。
    例如大纲写 Day 74 → Day 45（days_left 从74降到45），换算为正序：已过天数 = 90 - days_left。
    ch02: 90-74=16, 90-45=45，所以 day_range 写"第16-45天"。
{chapter_position_note}

## 全局设定
{game_settings_md}

## 章节大纲
{chapter_outline_md}

## 当前章节大纲
{current_chapter_outline_entry}

{previous_context}
## 前序状态快照
{state_snapshot_text}

此快照记录了到达本章时玩家可能的状态范围。请在「背景情境」中据此编写条件文本段。

## 已解锁的后续节点
{unlocked_nodes_text}

## 已关闭的后续节点
{locked_nodes_text}

## Markdown 模板（必须严格遵循）
{chapter_template_md}

## Few-shot 示例（格式参考，题材不同请勿抄袭叙事内容）
{few_shot_example_md}

注意：直接输出完整的 Markdown 文档。不要在末尾添加任何建议、下一步提示或向用户提问。"""

    return _pa_message(prompt)


# ============================================================
# Call 4: 全局一致性修订
# ============================================================

def build_call4_prompt(complete_script_md: str, user_feedback: str = "") -> list[ChatMessage]:
    """构建 Call 4（一致性修订）的 Prompt。

    Args:
        complete_script_md: 完整的剧本 Markdown（所有章节合并）
        user_feedback: 可选，用户的全局修订反馈（用于 L4 修订场景）
    """

    feedback_section = ""
    if user_feedback.strip():
        feedback_section = f"""
## 用户修订反馈
{user_feedback.strip()}

请在进行一致性检查的同时，根据以上反馈调整叙事细节。
"""

    system = "你是一位严肃游戏剧本审校编辑。你的任务是按剧本质量标准评分、识别必改问题，并判断该剧本是否可进入后续游戏制作。你只输出审校报告，不输出完整剧本。"

    user = f"""请对以下完整剧本进行质量审校。审校目标不是润色文字，而是判断它是否是一份可进入后续游戏制作的剧本制作包。

注意：不要输出完整剧本！只输出审校报告。引用原文时只引用关键片段，单处不超过 100 字。

{feedback_section}
## 完整剧本
{complete_script_md}

## 8 项质量评分标准

每项 1-5 分，总分 40 分。低于 3 分的单项必须标记为「必改」。总分低于 32 分，不建议进入后续制作。

1. 教学目标明确性：玩家完成游戏后应理解什么？每章和关键决策是否对应具体学习点？
2. 治理困境真实性：冲突是否来自制度、资源、利益、人情和时间约束，而不是强行戏剧化？
3. 角色驱动力：NPC 的行为是否由诉求、处境、关系和底线推动？
4. 选择张力：每个关键决策是否都有合理收益、真实代价和延迟后果？
5. 后果连续性：玩家选择是否影响后续章节、信息、NPC 态度、变量或结局？
6. 数值合理性：变量定义是否清晰，变化尺度是否稳定，阈值是否能触发具体事件？
7. 结局可解释性：每个结局是否能回溯到玩家路径和治理风格？
8. 制作可用性：文本、节点、选项、场景、变量和反馈是否能被前端与规则模块直接消费？

## 结构与机制硬检查

- 每章是否有 2-4 个核心决策点？如果只有 1 个，是否有充分制作理由？
- 每个决策点是否至少 3 个选项？如果有任何决策点只有 2 个选项，必须判为「必改」，且制作可用性不得高于 3 分。
- 每个选项是否列出 8 个变量影响、NPC 状态变化、flag 变化、解锁/关闭内容？
- 每章所有分支是否汇流到章节结算？
- 第 N 章的 next_chapter 是否指向第 N+1 章，最后一章是否进入 ending_evaluation？
- flag 是否先创建后引用？被关闭/解锁的节点是否真的在后文出现？
- 每个结局是否至少有一条可解释路径？
- 是否存在明显正确答案、纯惩罚选项或所有变量同时上升的神选项？

## 输出格式

输出 Markdown 格式的审校报告：

## 质量评分表
| 维度 | 分数（1-5） | 依据 | 是否必改 |
|---|---:|---|---|
| 教学目标明确性 |  |  | 是/否 |
| 治理困境真实性 |  |  | 是/否 |
| 角色驱动力 |  |  | 是/否 |
| 选择张力 |  |  | 是/否 |
| 后果连续性 |  |  | 是/否 |
| 数值合理性 |  |  | 是/否 |
| 结局可解释性 |  |  | 是/否 |
| 制作可用性 |  |  | 是/否 |

## 总分与制作判断
- 总分: X/40
- 是否建议进入后续制作: 是/否
- 判断理由: （2-3 句话）

## 必改项
对每个必改问题填写：
- **[必改] 问题标题**
  - 位置: 第X章 / 某节
  - 原文: （引用关键片段，不超过 100 字）
  - 影响: 说明它为什么会影响游戏制作或教学目标
  - 修改建议: 具体修改建议

## 建议优化项
列出非阻塞但会提升剧本质量的问题，每项包含位置和修改建议。

## 可进入人工编辑闭环的拆分建议
- 优先编辑的章节:
- 优先编辑的角色:
- 优先编辑的决策点:
- 优先编辑的结局:
- 需要补充的制作信息:"""

    return _flash_messages(system, user)


# ============================================================
# Call 5: JSON 抽取
# ============================================================

def build_call5_prompt(complete_script_md: str) -> list[ChatMessage]:
    """构建 Call 5（JSON 抽取）的 Prompt。"""

    system = "你是一个结构化数据提取器。你只负责从 Markdown 中提取已存在的信息，不创作、不新增、不修改任何内容。缺失字段填 null 或空数组。输出纯 JSON。"

    user = f"""请从以下 Markdown 剧本中提取章节剧情树 JSON。

## 完整剧本
{complete_script_md}

## 输出 JSON Schema

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
  "chapters": [
    {{
      "chapter_id": "string",
      "title": "string",
      "day_range": "string",
      "core_task": "string",
      "main_question": "string",
      "unlock_condition": null,
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
              "npc_state_changes": {{
                "npc_id": {{
                  "trust": "number",
                  "attitude": "number",
                  "anxiety": "number",
                  "known_info": ["string"],
                  "stance": "string"
                }}
              }},
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
        "variable_snapshot": {{}},
        "active_flags": ["string"],
        "unlocked_nodes": ["string"],
        "locked_nodes": ["string"],
        "summary": "string"
      }}
    }}
  ],
  "endings": [
    {{
      "ending_id": "string",
      "title": "string",
      "type": "string",
      "description": "string",
      "conditions": {{
        "variables": {{}},
        "flags_required": ["string"],
        "flags_forbidden": ["string"]
      }},
      "ending_text": "string",
      "teaching_summary": "string"
    }}
  ]
}}

## 抽取规则
1. 只抽取 Markdown 中明确存在的内容
2. 缺失字段填 null（对象）或 []（数组）或 0（数字）
3. 保留 chapter、info_node、choice、result、checkpoint、ending 的层级关系
4. 变量影响列表中的 8 个变量必须全部提取，即使某个值为 0
5. 每个选项的 NPC 状态变化必须提取到 npc_state_changes；没有明确变化时填 {{}}
6. 条件文本段（[若...]...[/若]）在 background 字段中保留原样
7. 输出合法 JSON，不要包含 markdown 代码块标记
8. 直接输出纯 JSON，不要在 JSON 前后添加任何说明文字"""

    return _flash_messages(system, user)


# ============================================================
# Call 6b: Qwen Flash 语义校验
# ============================================================

def build_call6b_prompt(script_json: dict, programmatic_issues: list[dict]) -> list[ChatMessage]:
    """构建 Call 6b（语义校验）的 Prompt。

    Args:
        script_json: Call 5 输出的完整 JSON
        programmatic_issues: Call 6a 程序化校验发现的问题列表
    """

    import json as _json

    issues_text = _json.dumps(programmatic_issues, ensure_ascii=False, indent=2)

    system = "你是一个剧本校验器。请对以下 JSON 剧本进行语义层面的深度检查。"

    user = f"""请检查以下问题：

1. 每个决策点的选项之间是否存在真实的策略张力？（不是简单地一个好一个坏）
2. Flag 的使用逻辑是否自洽？（flag 在被创建后才会被引用）
3. 结局条件是否合理？（不要求过严或过松）
4. 教学反馈是否与选项的实际影响一致？

## 程序校验已发现的问题
{issues_text}

## 完整剧本 JSON
{_json.dumps(script_json, ensure_ascii=False, indent=2)[:30000]}

## 输出格式
请输出一个 JSON 对象：
{{
  "valid": true/false,
  "semantic_issues": [
    {{
      "code": "SEMANTIC_001",
      "message": "问题描述",
      "severity": "error | warning",
      "location": "ch0X / option_X",
      "suggestion": "修复建议"
    }}
  ]
}}

只输出 JSON，不要任何说明文字。"""

    return _flash_messages(system, user)


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
  # 本章的决策点数量，2 ≤ N ≤ 4。决策点顺序执行，前一决策的结果影响后续决策的情境。
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

（本章包含 2-4 个顺序决策点。玩家依次面对，前一个决策的即时后果将作为下一个决策的情境铺垫。每个决策点必须有 3-5 个选项，不能只有 A/B 两个选项。）

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

（如有决策点 3、4，按同样格式追加。最后一个决策点的结果 next 指向 checkpoint。）

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
