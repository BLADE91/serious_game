# 剧本生成器 LLM 调用设计方案

## 1. 目标

本系统用于生成《底特律：变人》式的章节剧情树剧本。

整体路线采用：

```text
半结构化 Markdown 剧本生成
        ↓
Qwen Flash 抽取 JSON
        ↓
前端渲染章节剧情树
```

核心原则：

- 带 RAG 的主 LLM 负责生成 Markdown 剧本；
- Markdown 是人工评审和修改的母稿；
- Qwen Flash 负责从 Markdown 中抽取结构化 JSON；
- JSON 用于剧情树展示和运行逻辑；
- 不要求主 LLM 直接生成 JSON，避免影响剧本创作质量。

---

## 2. 整体调用流程

```text
用户输入主题 / 玩家角色 / 教学目标 / 约束条件
        ↓
Call 1：主 LLM 生成全局剧本设定
        ↓
Call 2：主 LLM 生成章节大纲
        ↓
Call 3：主 LLM 按章节逐章生成 Markdown 剧本
        ↓
Call 4：主 LLM 对完整 Markdown 做一致性检查与修订
        ↓
Call 5：Qwen Flash 从 Markdown 抽取 JSON
        ↓
Call 6：Qwen Flash / 程序校验 JSON
        ↓
前端渲染章节剧情树
```

---

## 3. 主 LLM 的生成原则

主 LLM 不直接一次性写完整长篇剧本，而是按章节生成。

每一章采用《底特律：变人》式章节流程：

```text
章节开始
  ↓
背景情境
  ↓
信息 / 调查节点
  ↓
核心决策点
  ├─ 选项 A → 结果 A
  ├─ 选项 B → 结果 B
  ├─ 选项 C → 结果 C
  └─ 选项 D → 结果 D
  ↓
章节结算
  ↓
下一章
```

要求：

1. 每章只设置一个核心决策点。
2. 每个核心决策点设置 3–4 个选项。
3. 每个选项必须有明确后果。
4. 每章分支最终汇流到章节结算。
5. 不生成无限展开的纯树。
6. 选择可以影响变量、解锁节点、关闭节点和结局条件。
7. 剧本输出格式为 Markdown，而不是 JSON。

---

## 4. Markdown 剧本模板

主 LLM 每一章必须按以下模板生成。

```markdown
# 第X章：章节标题

## 章节信息

- chapter_id: chXX
- day_range: 第X天 / 第X-Y天
- core_task: 本章核心任务
- main_decision: 本章核心决策问题
- learning_goals:
  - 教学目标1
  - 教学目标2

## 背景情境

玩家进入本章时面对的局面。  
要求控制在 300–500 字。

## 信息节点

### 信息节点1：节点标题

- node_id: chXX_info_01
- node_type: INFO
- next: chXX_choice_01

玩家可获得的信息、访谈、材料或现场观察。

### 信息节点2：节点标题

- node_id: chXX_info_02
- node_type: INFO
- next: chXX_choice_01

补充信息。

## 核心决策点

### 决策点：决策标题

- node_id: chXX_choice_01
- node_type: CHOICE
- question: 玩家需要做出的选择问题

#### 选项A：选项标题

- choice_id: chXX_A
- next: chXX_result_A
- 即时后果：
- 长期影响：
- 解锁节点：
  - node_id 或空数组
- 关闭节点：
  - node_id 或空数组
- 新增标记：
  - flag_id 或空数组
- 变量影响：
  - signed: +0
  - social_stability: -5
  - political_credit: +0
  - public_trust: +10
  - env_clue: +0
  - media_pressure: +0
- 教学反馈：

#### 选项B：选项标题

同上。

#### 选项C：选项标题

同上。

#### 选项D：选项标题

同上。

## 分支结果节点

### 结果A：结果标题

- node_id: chXX_result_A
- node_type: RESULT
- from_choice: chXX_A
- next: chXX_checkpoint

玩家选择 A 后看到的剧情结果。

### 结果B：结果标题

同上。

### 结果C：结果标题

同上。

### 结果D：结果标题

同上。

## 章节结算

- checkpoint_id: chXX_checkpoint
- node_type: CHECKPOINT
- merge_from:
  - chXX_result_A
  - chXX_result_B
  - chXX_result_C
  - chXX_result_D
- next_chapter: chXX+1
- 本章关键变量:
  - signed
  - social_stability
  - political_credit
  - public_trust
- 本章总结：
```

---

## 5. 全局变量表

主 LLM 和 Qwen Flash 都必须使用统一变量名。

第一版建议只保留以下变量：

```text
signed              签约户数
social_stability    社会稳定指数
political_credit    政治信用
public_trust        群众信任
env_clue            关键线索进度
media_pressure      舆情压力
budget              财政预算
days_left           剩余天数
```

要求：

- 主 LLM 不得自行新增变量；
- 如果确实需要局部状态，写入“新增标记 flags”，不要直接扩展全局变量；
- 变量变化统一写成 `变量名: +数值` 或 `变量名: -数值`；
- 单次变量变化一般控制在 -15 到 +15，关键选择可放宽到 ±25。

---

## 6. Call 1：生成全局设定

输入：

```text
主题
玩家角色
教学目标
目标章节数
目标结局数
风格要求
```

输出 Markdown：

```markdown
# 剧本全局设定

## 游戏主题

## 玩家角色

## 核心冲突

## 教学目标

## 全局变量表

## 主要角色

## 章节数量

## 结局类型
```

要求：

本阶段只生成设定，不写具体剧情。

---

## 7. Call 2：生成章节大纲

输入：Call 1 的全局设定。

输出 Markdown：

```markdown
# 章节大纲

## 第1章：标题

- chapter_id:
- core_task:
- main_decision:
- learning_goals:
- variables_in_focus:
- possible_branches:

## 第2章：标题

同上。
```

要求：

- 生成 6–8 个章节；
- 每章只有一个核心治理问题；
- 章节之间按主线时间推进；
- 不写详细剧情文本。

---

## 8. Call 3：逐章生成 Markdown 剧本

输入：

```text
全局设定
章节大纲
当前章节信息
前序章节摘要
固定 Markdown 模板
```

输出：当前章节完整 Markdown。

要求：

- 严格使用固定 Markdown 模板；
- 每章只生成一个核心决策点；
- 每个选项必须写清变量影响、解锁节点、关闭节点、新增标记和教学反馈；
- 所有分支必须汇流到章节结算；
- 不输出 JSON。

---

## 9. Call 4：全局一致性修订

输入：完整 Markdown 剧本。

让主 LLM 检查并修订：

```text
1. 每章是否只有一个核心决策点；
2. 章节之间是否衔接顺畅；
3. 变量名是否统一；
4. 是否存在未闭合分支；
5. 是否存在重复章节；
6. 是否存在过度小说化、难以抽取的段落；
7. 结局条件是否清晰；
8. 教学反馈是否足够明确。
```

输出：修订后的完整 Markdown。

---

## 10. Call 5：Qwen Flash 抽取 JSON

Qwen Flash 不负责创作，只负责结构抽取。

输入：完整 Markdown。

Prompt 要求：

```text
请从以下 Markdown 剧本中抽取章节剧情树 JSON。

要求：
1. 不改写剧情；
2. 不新增内容；
3. 不补全缺失信息；
4. 只抽取 Markdown 中明确存在的内容；
5. 缺失字段填 null 或空数组；
6. 保留 chapter、node、choice、result、checkpoint、ending 的层级关系；
7. 输出合法 JSON。
```

目标 JSON 结构：

```json
{
  "title": "",
  "variables": [],
  "chapters": [
    {
      "chapter_id": "",
      "title": "",
      "day_range": "",
      "core_task": "",
      "main_decision": "",
      "learning_goals": [],
      "nodes": [
        {
          "node_id": "",
          "node_type": "",
          "title": "",
          "text": "",
          "next": []
        }
      ],
      "choices": [
        {
          "choice_id": "",
          "text": "",
          "next": "",
          "immediate_result": "",
          "long_term_effect": "",
          "unlock_nodes": [],
          "lock_nodes": [],
          "flags_added": [],
          "effects": {},
          "teaching_feedback": ""
        }
      ],
      "checkpoint": {
        "checkpoint_id": "",
        "merge_from": [],
        "next_chapter": "",
        "summary": ""
      }
    }
  ],
  "endings": [
    {
      "ending_id": "",
      "title": "",
      "conditions": [],
      "ending_text": "",
      "teaching_summary": ""
    }
  ]
}
```

---

## 11. Call 6：JSON 校验

可以用 Qwen Flash 或程序规则校验。

检查项：

```text
1. 是否所有章节都有 chapter_id；
2. 是否所有节点都有 node_id；
3. 是否所有 choice 都有 next；
4. 是否所有 result 都汇流到 checkpoint；
5. 是否存在未定义变量；
6. 是否存在无法到达的节点；
7. 是否每章只有一个核心 choice；
8. 是否每个 choice 都包含 effects、unlock_nodes、lock_nodes、flags_added、teaching_feedback；
9. 是否所有章节都能连接到下一章或结局；
10. JSON 是否能用于前端画图。
```

如发现问题，返回问题列表，交给主 LLM 或人工修订 Markdown，而不是直接在 JSON 里硬改剧情。

---

## 12. 关键实现原则

1. **Markdown 是母稿**  
   人工修改和评审都基于 Markdown。

2. **JSON 是派生结构**  
   JSON 只用于前端渲染、剧情树展示和运行逻辑。

3. **主 LLM 负责创作**  
   它使用 RAG 写出有案例依据和教学价值的 Markdown 剧本。

4. **Qwen Flash 负责抽取**  
   它不参与剧情创作，只做 Markdown 到 JSON 的结构化转换。

5. **先章节大纲，后逐章生成**  
   不允许一次性生成完整长篇剧本。

6. **每章一个核心决策，章末汇流**  
   避免剧情树指数爆炸。

7. **所有选项必须包含机制字段**  
   包括变量变化、解锁节点、关闭节点、flag 和教学反馈。

---

## 13. 最小可行版本

第一版只需要支持：

```text
1 个全局设定
6–8 个章节
每章 1 个核心决策
每个决策 3–4 个选项
每章 1 个章节结算
3–4 个结局
Markdown 生成
JSON 抽取
HTML 剧情树展示
```

暂时不要做：

```text
复杂概率系统
大量隐藏节点
动态 NPC 记忆
过多局部变量
多轮自动平衡
```

先保证生成出的剧本稳定接近《底特律：变人》的章节剧情树形式。
