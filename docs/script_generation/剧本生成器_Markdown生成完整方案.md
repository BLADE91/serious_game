# 剧本生成器 Markdown 生成完整方案

## 1. 设计目标

生成《底特律：变人》式的章节剧情树剧本。Markdown 是母稿（人工评审和修改），JSON 是派生结构（前端渲染和运行逻辑）。

### 核心设计决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 章内分支处理 | **汇流到章节结算** | 避免指数爆炸，保持可管理 |
| 章间分支处理 | **Flag 门控 + 变量阈值** | 实现真正的路径依赖，消除马尔可夫性 |
| 章节生成方式 | **每章独立 LLM 调用** | 避免长对话漂移，每章质量独立可控 |
| 状态追踪 | **LLM 生成状态快照 + Flash 抽取校验** | 减轻主 LLM 负担，程序可校验 |
| 条件内容 | **`[若 flag/条件]...[/若]` 标记** | 同一章承载不同到达状态 |

---

## 2. 整体架构

### 2.1 叙事架构

```
第1章 ──→ 第2章 ──→ 第3章 ──→ ... ──→ 第N章 ──→ 结局判定

每章内部结构：
┌─────────────────────────────────────────────────┐
│ 背景情境（含条件文本段，反映不同到达状态）         │
│   ↓                                             │
│ 信息节点 × 2-3（可被 flag 解锁/关闭）            │
│   ↓                                             │
│ 核心决策点 × 1（3-4 选项，可被变量阈值灰显）      │
│   ├─ 选项A → 结果A ─┐                           │
│   ├─ 选项B → 结果B ─┤                           │
│   ├─ 选项C → 结果C ─┼─→ 章节结算（汇流）          │
│   └─ 选项D → 结果D ─┘                           │
│   ↓                                             │
│ 章节结算                                         │
│   · 变量范围快照                                 │
│   · 激活的 flag 集合                             │
│   · 解锁/关闭的后续节点清单                       │
└─────────────────────────────────────────────────┘
```

### 2.2 6 步生成管线

```
🖊️ PA Backend（主 LLM，创作）
  Call 1: 全局设定       →  game_settings.md
  Call 2: 章节大纲       →  chapter_outline.md（含决策框架 + 结局可达性验证）
  Call 3: 逐章生成 ×N    →  ch01.md ~ ch0N.md（每章独立调用）

⚡ Qwen Flash（轻量级，审校与抽取）
  Call 4: 一致性修订     →  complete_script.md（合并 + 检查 + 修补）
  Call 5: JSON 抽取      →  script_structure.json

🔧 程序规则 + Qwen Flash
  Call 6: 校验           →  validation_report.json（通过 / 问题清单）
```

**模型分配逻辑：**
- Call 1-3 使用 **PA Backend**（主 LLM）：需要长文本创作、叙事质量和逻辑一致性，属于创意密集型任务。
- Call 4-6 使用 **Qwen Flash**（轻量模型）：Call 4 是对已有文本的审校和修补，不需要创作能力；Call 5-6 是结构化抽取和规则检查。速度快、成本低。
- PA Backend 的 LLM 倾向于在输出末尾给出下一步建议或向用户提问，因此 Call 1-3 的 Prompt 末尾需要明确禁止此行为。Qwen Flash 无此倾向，Call 4-6 不需要此指令。

---

## 3. 变量系统

### 3.1 统一变量表

8 个全局变量，设计文档和代码统一使用：

| 变量名 | 中文名称 | 初始值 | 范围 | 语义 |
|---|---|---|---|---|
| `signed` | 签约户数 | 0 | 0-36 | 核心进度指标，直接关联任务完成 |
| `social_stability` | 社会稳定 | 70 | 0-100 | 群体事件风险，<40 触发预警事件，<20 强制坏结局 |
| `political_credit` | 政治信用 | 70 | 0-100 | 上级信任，<30 大量选项被锁，<20 强制坏结局 |
| `public_trust` | 群众信任 | 50 | 0-100 | 村民/社区支持度，影响信息获取和 NPC 合作 |
| `env_clue` | 环评线索 | 0 | 0-100 | 真相揭露进度，高值是达成好结局的必要条件之一 |
| `media_pressure` | 舆情压力 | 30 | 0-100 | 外部监督强度，高值解锁媒体相关选项，但也限制暗箱操作空间 |
| `budget` | 财政预算 | 8000 | 0-10000 | 万元，影响可用的行政手段和补偿方案 |
| `days_left` | 剩余天数 | 90 | 90→0 | 倒计时，降到 0 触发强制结局 |

### 3.2 变量变化规则

- 单次选项的变量变化控制在 **-15 到 +15**，关键选择可放宽到 ±25
- 每个选项**至少影响 2 个变量**，且方向不能全部相同（必须有得有失）
- 变量变化写成 `变量名: +数值` 或 `变量名: -数值`
- 主 LLM 不得自行新增变量；需要局部状态时使用 flag

---

## 4. Flag 系统

Flag 是实现真正路径依赖的核心机制。变量记录**量的累积**，flag 记录**质的差异**。

### 4.1 预设 Flag 类型

| 类别 | 命名前缀 | 示例 | 作用域 |
|---|---|---|---|
| 关系 | `flag_rel_` | `flag_rel_trusts_chief_wang` | 解锁 NPC 隐藏信息或特殊帮助 |
| 证据 | `flag_evi_` | `flag_evi_has_env_report` | 解锁调查类节点，是好结局的必要条件 |
| 事件 | `flag_event_` | `flag_event_ancestral_hall_reconciled` | 标记关键事件已发生 |
| 立场 | `flag_stance_` | `flag_stance_opposed_developer` | 影响 NPC 态度和选项可用性 |
| 策略 | `flag_strat_` | `flag_strat_used_media` | 标记采用的策略路线，影响结局 |

### 4.2 Flag 的三种作用方式

**1) 解锁节点：使后续章节的某个信息节点、决策选项出现**

```markdown
#### 选项C：寻求媒体监督
- 新增flag: [flag_strat_used_media]
- 解锁节点: [ch04_info_media_tip, ch05_choice_public_exposure]
```

**2) 关闭节点：使后续章节的某个节点/选项消失或灰显**

```markdown
#### 选项A：压制村民诉求
- 新增flag: [flag_event_suppressed_protest]
- 关闭节点: [ch03_info_villager_trust, ch06_choice_grassroots_support]
```

**3) 结局条件：flag 参与结局判定**

```markdown
### 结局示例：正义得以伸张
- conditions:
  - variables: {env_clue: ">= 80", signed: ">= 30", political_credit: ">= 30"}
  - flags_required: [flag_evi_has_env_report]
  - flags_forbidden: [flag_strat_secret_deal, flag_event_suppressed_protest]
```

### 4.3 Flag 生命周期

- **创建**：仅通过决策选项的 `新增flag` 字段
- **移除**：极少数选项可以 `移除flag`（例如「推翻之前的决定」）
- **查询**：后续章节的 `unlock_condition` 和 `可用条件` 引用
- **终结**：在结局条件中参与判定

---

## 5. Markdown 章节模板

### 5.1 完整模板

```markdown
# 第X章：章节标题

## 章节信息
- chapter_id: ch0X
- day_range: 第X-Y天（消耗 N 天）
- core_task: 本章核心任务（一句话）
- main_question: 本章核心决策问题（以问号结尾）
- unlock_condition: null
  # 如果本章本身需要前置条件才能进入，在此写明
  # 格式：{flags_required: [...], flags_forbidden: [...], variables: {...}}
  # 第一版暂不使用整章替换，此字段保留
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

**示例：**
> 督查组突然抵达镇里，要对签约进度进行专项检查。
>
> [若 flag_rel_trusts_chief_wang]
> 王镇长提前给你透了风声。他说督查组长是省里直接派的，之前处理过邻县的拆迁腐败案，不好糊弄。你有一晚时间准备材料。
> [/若]
>
> [若 social_stability < 60]
> 与此同时，村治保主任老李发来消息——至少五户已经签了字的村民，听说督查组来了，打算集体反悔。理由是「上面来人正好评评理」。
> [/若]
>
> 办公室的挂钟指向晚上十点。桌上的签约进度表显示已签 [signed] 户，距离总目标还差 [36 - signed] 户。你的预算还剩 [budget] 万元，距离截止日期还有 [days_left] 天。

## 信息节点

（2-3 个信息节点，提供决策所需的背景信息。部分节点可设置解锁条件。）

### 信息节点 1：节点标题

- node_id: ch0X_info_01
- node_type: INFO
- unlock_condition: null
  # 格式：{flags_required: [...], flags_forbidden: [...], variables: {...}}
- next: ch0X_choice

（150-300 字。玩家可获得的信息——访谈内容、材料发现、现场观察、数据报表等。）

### 信息节点 2：节点标题

- node_id: ch0X_info_02
- node_type: INFO
- unlock_condition: {flags_required: [flag_evi_has_env_report]}
  # 仅在前序章节获得了环评报告时才出现此信息节点
- next: ch0X_choice

（如果没有解锁，此节点在游戏中不出现。）

## 核心决策点

### 决策点：决策标题

- node_id: ch0X_choice
- node_type: CHOICE
- question: 玩家需要做出的选择问题（以问号结尾，清晰、有张力）

#### 选项 A：选项标题

- choice_id: ch0X_A
- option_label: A
- 选项文本: （玩家在选择界面看到的一句话描述，20-40 字）
- 可用条件: null
  # 格式：{variables: {political_credit: ">= 30", env_clue: ">= 50"}, flags_required: [...], flags_forbidden: [...]}
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

（同上结构）

#### 选项 C：选项标题

（同上结构）

#### 选项 D：选项标题

（同上结构，如只有 3 个选项则不写 D）

## 分支结果

### 结果 A：结果标题

- node_id: ch0X_result_A
- node_type: RESULT
- from_choice: ch0X_A
- next: ch0X_checkpoint

（200-400 字，玩家选择 A 后看到的完整叙事结果。应包含具体场景、NPC 反应、即时变化。）

### 结果 B / C / D

（同上）

## 章节结算

- checkpoint_id: ch0X_checkpoint
- node_type: CHECKPOINT
- merge_from:
  - ch0X_result_A
  - ch0X_result_B
  - ch0X_result_C
  - ch0X_result_D
- next_chapter: ch0Y
  # 下一章的 chapter_id；如果是最后一章，写 ending_evaluation

## 状态快照

**这是跨章节状态传递的关键数据结构。每章结束时必须填写，作为下一章生成的输入。**

### 变量范围（考虑本章所有可能路径）
| 变量 | 最低值 | 最高值 | 最可能路径 |
|---|---|---|---|
| signed | X | Y | Z（选项 X） |
| social_stability | X | Y | Z |
| political_credit | X | Y | Z |
| public_trust | X | Y | Z |
| env_clue | X | Y | Z |
| media_pressure | X | Y | Z |
| budget | X | Y | Z |
| days_left | X | Y | Z |

### 可能激活的 Flag 集合
（玩家到达本章结算时可能携带的所有 flag，标注来源选项）
- flag_xxx: 来自 ch0X_A（始终激活）
- flag_yyy: 来自 ch0X_C（始终激活）
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
```

### 5.2 条件文本段语法

在「背景情境」和「信息节点」的内容文本中，使用以下标记：

```
[若 flag_xxx] 或 [若 flag_xxx == true]
  仅携带此 flag 的玩家看到这段文本
[/若]

[若 !flag_xxx] 或 [若 flag_xxx == false]
  仅未携带此 flag 的玩家看到这段文本
[/若]

[若 variable >= N]
  仅满足变量条件的玩家看到这段文本
[/若]

[若 flag_xxx 且 variable >= N]
  组合条件
[/若]
```

**规则：**
- 条件标记内的文本是**额外追加**的，不是替换默认文本
- 主文本描述通用情况，条件文本补充差异化细节
- LLM 必须在每个条件后面提供默认情况（无 flag 时的文本）
- 条件文本段不嵌套

---

## 6. 六步生成流程

### Call 1：生成全局设定

**输入：**（由用户在 UI 或 CLI 中填写，以下为示例）

```yaml
主题: "<用户填写——例如：生态搬迁中的基层治理困境>"
玩家角色: "<用户填写——例如：挂职副镇长，主管搬迁签约工作>"
教学目标:
  - "<用户填写>"
  - "<用户填写>"
目标章节数: 6          # 建议 6-8，用户可调
目标结局数: 3-4        # 用户指定，Call 1 据此生成
风格要求: "<用户填写——例如：现实主义基层政治题材，避免说教>"
```

**Prompt 结构：**

```
你是一位严肃游戏剧本设计师，专精于公共管理题材。
你的任务是生成一个游戏的全局设定。你现在不写剧情，只做设定。

请根据以下信息，生成游戏的全局设定 Markdown。

[用户输入：主题、角色、教学目标等]

请按以下模板输出：

# 剧本全局设定

## 游戏主题
（200-300 字）

## 玩家角色
- 姓名:
- 职位:
- 背景:
- 初始立场:
- 核心动机:

## 核心冲突
（300-500 字，描述主要矛盾、利益相关方、冲突升级路径）

## 教学目标
（从输入中整理）

## 全局变量表
（列出 8 个变量及初始值）

## 主要角色
根据故事需要设计 NPC。对每位角色填写：
- npc_id:
- 姓名:
- 身份:
- 与玩家的关系:
- 核心诉求:
- 行为逻辑:
- 可能立场变化:

## 章节数量: [由用户输入决定]

## 结局类型（数量由用户指定的目标结局数决定）
对每个结局填写：
- ending_id:
- 标题:
- 类型: good / neutral / bad
- 一句话描述:
- 核心条件概述（用自然语言描述，不写具体数值阈值）

## 风格与基调
（100-200 字）

## 参考文献与案例来源
（列举 3-5 个参考案例或文献）

注意：直接输出完整的 Markdown 文档。不要在末尾添加任何建议、下一步提示或向用户提问。
```

**输出：** `game_settings.md`

---

### Call 2：生成章节大纲

**输入：** `game_settings.md`（完整文本）

**Prompt 结构：**

```
你是一位游戏剧情架构师。你的任务是基于全局设定，设计章节大纲。
章节数量由全局设定决定。每章只有一个核心决策点。你不写详细剧情，只搭骨架。

请基于以下全局设定，生成章节大纲。

[game_settings.md 全文]

请按以下模板输出：

# 章节大纲

## 结局可达性验证

（目的：确保 Call 1 中定义的每个结局，在本大纲的 flag 结构和变量流向下是可达的。
此处不预设具体的选择序列——那是玩家的事。此处验证的是：存在至少一组选择能到达该结局。）

对 Call 1 中定义的每个结局，填写：

### 结局 [ending_id]：[标题]（类型: good/neutral/bad）

- 需要的最终变量状态: （例如 env_clue 高, signed 高, political_credit 不低）
- 必须携带的 flag: （例如 flag_evi_has_env_report）
- 必须避免的 flag: （例如 flag_strat_secret_deal）
- 在大纲中如何达成:
  - 哪些章的选项可以累积所需变量？（列出章号和选项标签，例如 ch03_A/B 均可增加 env_clue）
  - 哪些章的选项会设置必须 flag？（列出章号和选项标签）
  - 哪些章的选项会设置禁止 flag？（必须被避免的选择）
- 是否存在矛盾？（例如：要同时拿到 flag_X 和 flag_Y，但它们在互斥的选项中）如有矛盾，调整大纲。

如果某个结局在当前大纲下不可达，此节必须明确指出，并建议修改具体章节的 decision_framework 以打开路径。

## 第 1 章：标题
- chapter_id: ch01
- day_range:
- core_task:
- main_question:
- decision_framework:
  - 选项 A（标签）: 核心逻辑 → 主要变量影响方向 → 设置的 flag
  - 选项 B（标签）: ...
  - 选项 C（标签）: ...
  - 选项 D（标签）: ...
- variables_in_focus: [本章重点关注的变量]
- flag_design: [本章可能设置的 flag 及设计意图]
- learning_goals:

## 第 2 章：标题
（同上）

...（共 N 章，N = 全局设定中定义的章节数量）

## Flag 全局规划表

| flag_id | 创建于 | 作用（解锁什么 / 关闭什么） | 参与哪个结局 |
|---|---|---|---|
| flag_xxx | ch0X_A | 解锁 ch0Y_info_Z | ending_01 |
| ... | ... | ... | ... |

（「参与哪个结局」填写 Call 1 中定义的 ending_id。）

注意：直接输出完整的 Markdown 文档。不要在末尾添加任何建议、下一步提示或向用户提问。
```

**输出：** `chapter_outline.md`

---

### Call 3：逐章生成 Markdown 剧本

这是整个管线中最关键的调用。**每章独立调用，不累积对话上下文。**

**输入（每次调用）：**

1. `game_settings.md`（全局设定，完整）
2. `chapter_outline.md`（章节大纲，完整）
3. 当前章节的大纲条目
4. **状态快照**（从上一章的「状态快照」节提取，第 1 章则为初始状态）
5. 固定的 Markdown 模板（即第 5 节的模板）
6. 一个 few-shot 示例章节（随 Prompt 一起发送，帮助 LLM 理解格式）

**Prompt 结构：**

```
你是一位严肃游戏剧本作家。请生成第 X 章的剧本 Markdown 文本。

核心原则：
1. 严格遵循模板格式。模板中的每一个字段都必须填写。
2. 每章只有一个核心决策点。
3. 每个选项必须有明确的变量影响（8 个变量全部列出）、flag 变化、解锁/关闭节点。
4. 章内所有分支必须汇流到章节结算。
5. 在背景情境中使用 [若 flag]...[/若] 和 [若 变量>=N]...[/若] 反映不同到达状态。
6. 叙事要有文学质感，但不能写成纯小说。保持 300-500 字的紧凑叙事。
7. 决策选项之间必须存在真实的伦理或策略张力——没有显然正确的选项。
8. 每个选项的教学反馈必须从公共管理视角点明治理逻辑和代价。
9. 变量变化必须有得有失——不存在所有变量同时上升的神选项。
10. 不确定的地方做出合理假设，不要 request_clarification。

## 全局设定
[game_settings.md 全文]

## 章节大纲
[chapter_outline.md 全文]

## 当前章节大纲
[当前章的大纲条目]

## 前序状态快照
[上一章的「状态快照」节完整内容；第 1 章则是初始变量值和空 flag]
此快照记录了到达本章时玩家可能的状态范围。请在「背景情境」中据此编写条件文本段。

## 已解锁的后续节点
[从状态快照中提取的已解锁节点清单]

## 已关闭的后续节点
[从状态快照中提取的已关闭节点清单]

## Markdown 模板
[第 5 节的完整模板]

## Few-shot 示例
[一个已填写好的示例章节，展示所有字段的正确格式]

注意：直接输出完整的 Markdown 文档。不要在末尾添加任何建议、下一步提示或向用户提问。
```

**输出：** `ch0X.md`

**重要：** 生成第 N+1 章之前，需要先从第 N 章的 Markdown 中提取「状态快照」节，作为第 N+1 章的输入。提取工作可以由 Qwen Flash 完成（轻量级），也可以由简单的正则匹配完成。

---

### Call 4：全局一致性修订

**输入：** 完整的剧本 Markdown（所有章节合并为一个文件）

**Prompt 结构：**

```
你是一位剧本编辑。你的任务是对已生成的完整剧本进行一致性检查和修订。
你只能修改明确有问题的地方，不要重写整个剧本。

请对以下完整剧本进行一致性检查。逐项检查，发现问题后修补。

[完整的剧本 Markdown]

## 检查清单

### 结构检查
1. 每章是否严格只有一个核心决策点（node_type: CHOICE）？
2. 每章是否至少有 3 个选项？
3. 每章的所有分支结果是否都汇流到章节结算？
4. 每章是否都有完整的状态快照？

### 衔接检查
5. 第 N 章的 next_chapter 是否指向第 N+1 章？
6. 第 N 章状态快照中的变量范围是否与第 N+1 章背景情境一致？
7. 第 N 章解锁/关闭的节点是否在第 N+1 及后续章节中正确体现？
8. Flag 的创建和使用是否一致？（flag 在被创建后才被引用）

### 变量检查
9. 所有变量名是否统一？（8 个变量，不能出现未定义的变量名）
10. 每章的变量范围是否合理？（不超过 ±25 单次变化）
11. 从第 1 章到最后一章的变量累积是否在合理范围内（不溢出）？

### 结局可达性检查
12. 每个结局是否至少有一条可达到的路径？（参考大纲中的「结局可达性验证」）
13. 大纲中为每个结局分析的 flag 和变量条件是否在本剧本中确实可被满足？
14. 是否存在理论上不可达的结局？如有，标记并建议修补。

### 叙事质量检查
15. 是否存在过度小说化的段落（超过 500 字的连续叙事）？
16. 决策选项之间是否存在真实张力？（无显然正确/错误的选项）
17. 教学反馈是否足够明确？
18. 条件文本段是否覆盖了主要的到达状态？

## 输出格式

请输出修订后的完整剧本 Markdown，并在末尾附上：

## 修订记录
- 修改 X: [原问题] → [修改内容]
- ...

```

**输出：** 修订后的 `complete_script.md`（含修订记录）

---

### Call 5：Qwen Flash 抽取 JSON

**输入：** `complete_script.md`

**Prompt 结构：**

```
你是一个结构化数据提取器。你只负责从 Markdown 中提取已存在的信息，
不创作、不新增、不修改任何内容。缺失字段填 null 或空数组。输出纯 JSON。

请从以下 Markdown 剧本中提取章节剧情树 JSON。

[complete_script.md 全文]

## 输出 JSON Schema

{
  "title": "string",
  "player_role": "string",
  "core_conflict": "string",
  "variables": [
    {"name": "string", "chinese_name": "string", "initial_value": "number", "range": "string"}
  ],
  "initial_state": {"signed": 0, "social_stability": 70, ...},
  "chapters": [
    {
      "chapter_id": "string",
      "title": "string",
      "day_range": "string",
      "core_task": "string",
      "main_question": "string",
      "unlock_condition": null,
      "learning_goals": ["string"],
      "background": "string",
      "info_nodes": [
        {
          "node_id": "string",
          "title": "string",
          "content": "string",
          "unlock_condition": null,
          "next": "string"
        }
      ],
      "decision_point": {
        "node_id": "string",
        "question": "string",
        "options": [
          {
            "choice_id": "string",
            "option_label": "string",
            "text": "string",
            "availability": null,
            "effects": {
              "signed": "number",
              "social_stability": "number",
              "political_credit": "number",
              "public_trust": "number",
              "env_clue": "number",
              "media_pressure": "number",
              "budget": "number",
              "days_left": "number"
            },
            "unlock_nodes": ["string"],
            "lock_nodes": ["string"],
            "flags_added": ["string"],
            "flags_removed": ["string"],
            "immediate_result_text": "string",
            "long_term_effect": "string",
            "teaching_feedback": "string"
          }
        ]
      },
      "results": [
        {
          "node_id": "string",
          "from_choice": "string",
          "text": "string",
          "next": "string"
        }
      ],
      "checkpoint": {
        "checkpoint_id": "string",
        "merge_from": ["string"],
        "next_chapter": "string",
        "variable_snapshot": {},
        "active_flags": ["string"],
        "unlocked_nodes": ["string"],
        "locked_nodes": ["string"],
        "summary": "string"
      }
    }
  ],
  "endings": [
    {
      "ending_id": "string",
      "title": "string",
      "type": "string",
      "description": "string",
      "conditions": {
        "variables": {},
        "flags_required": ["string"],
        "flags_forbidden": ["string"]
      },
      "ending_text": "string",
      "teaching_summary": "string"
    }
  ]
}

## 抽取规则
1. 只抽取 Markdown 中明确存在的内容
2. 缺失字段填 null（对象）或 []（数组）或 0（数字）
3. 保留 chapter、info_node、choice、result、checkpoint、ending 的层级关系
4. 变量影响列表中的 8 个变量必须全部提取，即使某个值为 0
5. 条件文本段（[若...]...[/若]）在 background 字段中保留原样
6. 输出合法 JSON，不要包含 markdown 代码块标记
7. 直接输出纯 JSON，不要在 JSON 前后添加任何说明文字。
```

**输出：** `script_structure.json`

---

### Call 6：校验

**分两步：程序自动校验 + Qwen Flash 语义校验**

#### Step 6a：程序自动校验（确定性规则）

```python
# 校验规则（伪代码）
errors = []

# 结构完整性
for chapter in json["chapters"]:
    if not chapter["chapter_id"]: errors.append("缺失 chapter_id")
    if not chapter["decision_point"]: errors.append(f"{chapter['chapter_id']} 缺失决策点")
    if len(chapter["decision_point"]["options"]) < 3:
        errors.append(f"{chapter['chapter_id']} 选项少于 3 个")
    for opt in chapter["decision_point"]["options"]:
        if len(opt["effects"]) != 8:
            errors.append(f"{opt['choice_id']} 变量影响不足 8 个")

# 衔接性
for i in range(len(chapters) - 1):
    if chapters[i]["checkpoint"]["next_chapter"] != chapters[i+1]["chapter_id"]:
        errors.append(f"第 {i+1} 章 next_chapter 不指向第 {i+2} 章")

# 变量范围
for chapter in chapters:
    for var in ["signed", "budget", "days_left"]:
        # 检查是否有溢出（signed > 36, budget < 0 等）
        pass

# Flag 一致性
all_created_flags = set()
for chapter in chapters:
    for opt in chapter["decision_point"]["options"]:
        for flag in opt["flags_added"]:
            all_created_flags.add(flag)
all_referenced_flags = set()
# ... 收集所有被引用的 flag
uncreated = all_referenced_flags - all_created_flags
if uncreated: errors.append(f"引用了未创建的 flag: {uncreated}")

# 结局可达性（简单检查：每个结局的条件变量是否在合理范围内）
for ending in json["endings"]:
    # 检查变量条件是否可能达到
    pass
```

#### Step 6b：Qwen Flash 语义校验

只对程序校验发现的警告项和 Flag 逻辑进行深度检查。

```
你是一个剧本校验器。请检查以下问题：

1. 选项 A 和 B 之间是否存在真实的策略张力？（不是简单地一个好一个坏）
2. Flag 的使用逻辑是否自洽？（flag 在被创建后才会被引用）
3. 结局条件是否合理？（不要求过严或过松）
4. 教学反馈是否与选项的实际影响一致？

[对每个有疑问的章节给出具体判断]
```

**输出：** `validation_report.json`（通过 / 问题清单 → 返回 Call 4 修订）

---

## 7. 状态传递机制（关键流程）

章节间状态传递是整个系统最关键的工程问题。以下是详细流程：

```
第 1 章生成
  输入: game_settings + outline + 初始状态（8 个变量初始值 + 空 flag 集合）
  输出: ch01.md（含「状态快照」节）
    ↓
提取状态快照（Qwen Flash 轻量抽取 或 正则匹配）
  输出: state_snapshot_ch01.json
    ↓
第 2 章生成
  输入: game_settings + outline + state_snapshot_ch01 + 解锁/关闭节点清单
  输出: ch02.md（含「状态快照」节）
    ↓
...（重复至最后一章）
```

### 状态快照提取（轻量级）

状态快照在 Markdown 中有固定的表格和列表格式，可以用正则提取，也可以用 Qwen Flash 做一次性提取。**建议用 Qwen Flash 一次性提取所有章节的 Markdown**（在 Call 3 全部完成后），然后以提取出的 JSON 作为 Call 4 和 Call 5 的输入。

实际流程优化为：

```
Call 3a: 生成 ch01（输入初始状态） → ch01.md
Call 3b: 生成 ch02（输入 ch01 的状态快照文本） → ch02.md
Call 3c: 生成 ch03（输入 ch02 的状态快照文本） → ch03.md
...

然后：

Call 3 后处理（Flash 批量提取）: N 个 ch0X.md → N 个 state_snapshot_ch0X.json
  → 程序校验状态快照的一致性
  → 如有不一致，用定向 prompt 修补具体章节

Call 4: 一致性修订（此时已有准确的状态快照 JSON 作为参考）
```

---

## 8. 结局系统

### 8.1 结局判定逻辑

```
游戏结束条件：
  - 最后一章结算后
  - days_left 降至 0（强制结束，即使未到最后章）

优先级从高到低检查（以下为示例逻辑，实际结局及其条件由 Call 1 定义）：

  1. 坏结局（最优先）：触发失败条件（如关键变量跌破阈值）
  2. 好结局（次优先）：满足最严格的变量 + flag 组合
  3. 中性结局：满足中等条件
  4. 默认结局（兜底）：以上均不满足
```

### 8.2 结局模板

```markdown
## 结局 X：结局标题

- ending_id: ending_X
- ending_type: good / neutral / bad
- priority: 1（最高）/ 2 / 3 / 4（默认）
- conditions:
  - variables: {var1: ">= N", var2: "<= M"}
  - flags_required: [flag_xxx, flag_yyy]
  - flags_forbidden: [flag_zzz]

### 结局叙事
（300-500 字，描述结局场景。应呼应玩家在整个游戏中的关键选择。）

### 关键变量终值展示
（列出 8 个变量的终值范围）

### 教学总结
（200-300 字，从公共管理视角总结此结局的治理逻辑得失）
```

---

## 9. Few-shot 示例章节

在 Call 3 的每次 Prompt 中附带一个完整的示例章节（约 1500-2000 字），帮助 LLM 稳定输出格式。示例应选择一个与当前题材不同的主题，避免 LLM 抄袭叙事内容。

示例应展示：
- 背景情境中的条件文本段
- 3 个选项的完整字段
- 状态快照的表格格式
- 所有 8 个变量的变化

---

## 10. 质量保障机制

### 10.1 每章生成后的即时检查

在 Call 3 每章生成后，用正则快速检查：
- 是否有 `## 状态快照` 节
- 是否有 `## 章节结算` 节
- 选项数量 ≥ 3
- 每个选项是否有 `变量影响` 列表

不合格则重试（最多 2 次）。

### 10.2 状态快照一致性检查

提取所有状态快照后，程序检查：
- 第 N+1 章的变量范围是否与第 N 章的快照一致（不能凭空出现新值）
- Flag 是否在被创建后才被引用
- 解锁/关闭节点清单是否跨章一致

### 10.3 人工评审入口

Markdown 是母稿。在 Call 3 完成后、Call 4 之前，人工可以：
- 直接修改任何章节的 Markdown
- 增删 flag
- 调整变量数值
- 修改叙事文本
- 然后在修订后的 Markdown 上继续 Call 4 → Call 5

---

## 11. 剧本修改流程

剧本生成后，迭代修改才是常态。修改分为四个层级，从轻到重。

### 层级总览

| 层级 | 场景 | 改什么 | 模型 | Call 4 | 下游影响 |
|---|---|---|---|---|---|
| L1 人工直改 | 用户直接编辑 Markdown | 任意 | 无 | 建议跑 | 极小 |
| L2 单元素修订 | 「ch03 选项 B 代价太小」 | 单个字段 | Flash | 建议跑 | 状态快照微调 |
| L3 单章重生成 | 「第 3 章整个重写」 | 整章 | PA Backend | **必须跑** | 下游 flag/变量依赖可能断裂 |
| L4 全局修订 | 「整体基调太乐观了」 | 多章 | PA Backend | 本质就是 Call 4 | 全面重建 |

### 公共后置流程

无论哪个层级，修改之后都走同一个后置管线：

```
修改完成
  ↓
Call 4：一致性修订（必选 / 建议 / 可选）
  ↓
Call 5：JSON 抽取
  ↓
Call 6：校验
  ↓
前端重新渲染
```

**Call 4 在这里的角色变了**：首次生成时它是全局审校；修改场景中它是**一致性安全网**——检查修改有没有引入断裂（flag 引用失效、变量范围不匹配、结局不可达等）。

---

### L1：人工直改

用户直接编辑 `complete_script.md`。不需要 LLM。

**流程：**
```
1. 用户编辑 Markdown
2. [建议] 跑 Call 4 检查一致性
3. 跑 Call 5 + Call 6 重新抽取 JSON
```

**适用场景：** 改错别字、调整叙事措辞、微调变量数值、增删一句条件文本段。

---

### L2：单元素修订

用户提自然语言指令，Qwen Flash 定位并修补单个字段。

**输入：**
- 目标章节 Markdown
- 修改指令（自然语言）
- 约束上下文（前章状态快照 + 该章创建的 flag 在后续被引用的情况）

**Prompt 结构：**
```
你是一个剧本修订器。请按以下指令修改目标章节。只修改受影响的字段，保持其他内容不变。

## 目标章节
[ch0X.md 全文]

## 修改指令
[用户反馈，例如：把选项 B 的政治信用代价从 -5 改成 -15，相应修改即时后果文本]

## 受此章影响的后续内容
- ch0Y 依赖 flag: [flag_xxx]（由本章 ch0X_A 创建）
- ch0Z_info_02 的解锁条件引用 flag_xxx
- 本章的变量范围变化可能影响后续章节的背景情境条件文本

## 输出要求
- 只修改与指令直接相关的字段
- 如果修改了变量影响，同步更新状态快照
- 如果修改了 flag，标注哪些后续章节需要检查
- 输出完整的修订后章节 Markdown
```

**输出：** 修订后的 `ch0X.md`

**流程：**
```
1. 用户指定章节 + 修改指令
2. Qwen Flash 修订该章节
3. [建议] 跑 Call 4 检查下游一致性
4. 跑 Call 5 + Call 6
```

---

### L3：单章重生成

用户对某一章不满意，整体重写。用 **PA Backend**（创作行为）。

**流程：**
```
1. 用户提出重写意见
2. PA Backend 重新生成该章
   输入：全局设定 + 大纲 + 前章状态快照 + 用户反馈
3. 该章状态快照更新
4. ⚠️ 必须跑 Call 4
   Call 4 会检测：
   - 后续章节引用的 flag 是否仍然被创建
   - 后续章节的变量假设是否仍然成立
   - 结局可达性是否仍然满足
   Call 4 自动修补小问题，无法自动修补的列入报告
5. 跑 Call 5 + Call 6
```

**关键设计：下游影响检测**

Call 4 在修改场景中需要额外检查：
```
1. Flag 断裂检测：
   - 后续章节 unlock_condition 引用了被删除的 flag → 标记
   - 后续章节 关闭节点 引用了被删除的 flag → 标记
2. 变量范围检测：
   - 后续章节背景情境的条件文本假设了某个变量范围
   - 新生成的章节导致该范围偏移 → 标记
3. 结局可达性检测：
   - 修改后，原先可达的结局是否仍然可达
```

对于 L3，检测到问题后方案：
- **自动可修**：后续章节的背景情境微调、变量数值的简单调整
- **人工决策**：flag 被删除需要重新设计后续依赖时，生成报告让用户决定

---

### L4：全局修订

用户对整体不满意，需要跨章调整。本质就是**带用户反馈的 Call 4**。

**流程：**
```
1. 用户提出全局反馈（例如：「整体基调太乐观」「决策选项之间的代价差异太小」）
2. 重新跑 Call 4，Prompt 中注入用户反馈
3. Call 4 逐章修订，输出完整 Markdown
4. 跑 Call 5 + Call 6
```

**与首次 Call 4 的区别：**
- 首次 Call 4：只检查、修补 Markdown 中明确的问题
- 修订 Call 4：基于用户反馈主动调整叙事细节，而不仅仅是修补格式

如果改动太大导致大纲级问题（例如：需要新增一章、需要重新设计结局条件），那就需要回到 Call 2 重新设计大纲——但这种情况少见。

---

### 修订历史

建议在 `complete_script.md` 末尾维护修订记录：

```markdown
## 修订记录

| 版本 | 日期 | 层级 | 修改内容 | 影响范围 |
|---|---|---|---|---|
| v1.0 | ... | — | 首次生成 | 全部 |
| v1.1 | ... | L2 | ch03 选项 B 代价调大 | ch03 状态快照 |
| v1.2 | ... | L3 | 重写 ch04 | ch04-ch06 状态一致性 |
```

每次 Call 4 自动追加一条记录。

---

## 12. 前端剧情树渲染

从 JSON 到 HTML 可视化：

### 12.1 核心展示元素

- **每个结局一条路径线**，时间轴对齐在每章决策列上
- **每列 = 一章的核心决策点**
- **节点内显示**：选项字母 + 简短描述
- **条件节点**（有 unlock_condition）：虚线边框 + 浅色背景
- **灰显选项**（不满足可用条件）：灰色文字 + 锁图标
- **Flag 标注**：节点下方小标签显示设置的 flag

### 12.2 交互

- 悬停节点：显示完整选项文本 + 变量影响 + 教学反馈
- 点击选项：高亮从该选项向下游可达的所有节点
- 结局展示：4 个结局卡片，标注触发条件

### 12.3 技术方案

- 纯静态 HTML + SVG（与现有 `decision_tree.html` 类似）
- 从 JSON 文件读取数据
- 浅色主题

---

## 附录 A：与现有实现的对齐

| 现有实现 | 新方案 | 变更说明 |
|---|---|---|
| 3 幕结构（Act 1/2/3） | N 章结构（用户可配） | 粒度变细，每章一个决策 |
| 变量名不一致 | 8 个统一变量 | 代码和 Prompt 全部对齐 |
| 无 Flag 系统 | 双层门控（Flag + 变量） | 新增 Flag 机制 |
| ReAct Agent 自行检索 | 保留，但检索结果注入 Call 3 Prompt | 不改变检索逻辑 |
| 分支仅靠变量阈值 | Flag 门控节点 + 变量阈值选项 | 真正的非马尔可夫分叉 |
| 单次生成全部剧本 | 逐章独立调用 | 改为循环调用 |
| Qwen Flash 多步抽取 | 简化为单次抽取 | 合并抽取步骤 |

## 附录 B：风险与缓解

| 风险 | 可能性 | 缓解措施 |
|---|---|---|
| LLM 不遵循模板格式 | 中 | Few-shot + 即时正则检查 + 重试 |
| 状态快照计算错误 | 中 | Flash 抽取 + 程序校验 + 人工抽查 |
| Flag 爆炸（太多 flag） | 低 | 每章最多 4 个新 flag，大纲预分配 |
| 结局不可达 | 中 | Call 2 预分配 + Call 4 可达性检查 |
| 叙事质量下降（模板太死） | 中 | 模板只约束结构，叙事文本自由发挥 |
| Token 消耗过大 | 高 | 独立调用增加 token，但质量优先 |
