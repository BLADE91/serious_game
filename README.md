# 父母官 · 严肃游戏剧本生成器

《父母官》是一款基层治理题材的严肃游戏（约 45 分钟）。本工具通过 LLM 自动生成结构化游戏剧本，包括三幕叙事、NPC 关系网络、多选项决策点序列和多结局条件。支持 Web 界面实时预览和人工反馈修订。

## 核心能力

- **结构化剧本生成**：7 阶段流水线，产出包含三幕结构、NPC 关系网（6 种关系类型）、18 个左右多选项决策点（每点 3-5 个选项）和 3-4 个结局的完整剧本
- **Web 界面**：单文件前端，填写场景/角色/目标 → 实时进度条 → 结果分面板展示 → 反馈修订
- **双后端支持**：DashScope Qwen 或 PA Backend Agent，通过 `.env` 切换
- **实时取消**：支持中断正在进行的 HTTP 请求，无需等待超时
- **CLI 工具**：命令行直接生成，适合批量或调试场景

## 环境准备

需要 Python 3.10+，安装依赖：

```bash
pip install -r requirements.txt
```

复制并编辑配置文件：

```bash
cp .env.example .env
```

### .env 配置说明

```env
# ---- LLM 后端（二选一）----
SCRIPT_GENERATION_BACKEND=pa_backend    # qwen 或 pa_backend

# Qwen 模式（DashScope API）
DASHSCOPE_API_KEY=your_key_here
QWEN_MODEL=qwen-plus

# PA Backend 模式
PA_BACKEND_BASE_URL=https://apitest.know-pa.cn
PA_BACKEND_ACCOUNT=your_account
PA_BACKEND_PASSWORD=your_password
PA_BACKEND_SUPABASE_URL=https://your-project.supabase.co
PA_BACKEND_SUPABASE_KEY=your_key

# 检索（Qwen 模式使用）
OPENSEARCH_HOST=your_host
OPENSEARCH_PORT=9200
OPENSEARCH_USERNAME=admin
OPENSEARCH_PASSWORD=your_password
OPENSEARCH_INDEX=serious_game_sources
```

> PA Backend 模式下检索由 agent 端处理，无需配置 OpenSearch。

## 使用方式

### 方式一：Web 界面（推荐）

```bash
python run_server.py --reload
```

浏览器打开 `http://localhost:8000`，填写：

| 字段 | 说明 | 示例 |
|------|------|------|
| 政策场景 | 游戏主题 | 生态搬迁、征地拆迁 |
| 玩家角色 | 玩家扮演的身份 | 乡镇党委副书记 |
| 教育目标 | 游戏的学习目的 | 体验基层政策执行中多重压力的权衡 |
| 时长 | 目标游戏时长 | 45 分钟 |
| 复杂度 | 简单/中等/复杂 | 中等 |
| 额外要求 | 自由文本补充 | 需要涉及宗族矛盾 |

生成完成后可在结果面板查看三幕结构、NPC 关系网、决策点和结局，也可在底部反馈框输入意见进行定向修订。

API 文档：`http://localhost:8000/docs`

### 方式二：命令行

```bash
# 结构化输入（推荐）
python run_script_generation.py \
  --scenario "生态搬迁" \
  --player-role "乡镇党委副书记" \
  --learning-goal "体验基层政策执行中的多重压力" \
  --duration 45

# 紧凑模式（单次生成，适合快速迭代）
python run_script_generation.py --compact "生成一个生态搬迁剧本"

# 修订已有剧本
python run_script_generation.py --revise outputs/script_drafts/上一次.json \
  --feedback "NPC 关系网太简单，增加更多矛盾冲突"
```

生成结果保存在 `outputs/script_drafts/`。

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | Web 前端页面 |
| `POST` | `/api/generate` | SSE 流式生成，7 阶段进度推送 |
| `POST` | `/api/revise` | SSE 流式修订，基于反馈定向修改 |
| `POST` | `/api/cancel/{task_id}` | 取消进行中的生成（中断 HTTP 连接） |
| `GET` | `/docs` | Swagger API 文档 |

## 输出结构

```json
{
  "script": {
    "title": "父母官：生态搬迁中的权衡与抉择",
    "premise": "故事设定",
    "player_role": "玩家角色",
    "core_conflict": "核心冲突",
    "initial_game_state": {
      "day": 1, "action_points": 3,
      "budget_remaining": 8000, "budget_unit": "万元",
      "signed_households": 0, "total_households": 36,
      "social_stability_index": 70,
      "political_credit": 70,
      "cadre_execution_index": 60
    },
    "acts": [
      {
        "act_number": 1, "title": "开局破冰",
        "day_range": "第1-15天", "goal": "目标",
        "description": "阶段概述",
        "decision_point_ids": ["DP01", "DP02", "..."]
      }
    ],
    "npc_seed": [
      {
        "npc_id": "cadre_01", "name": "张书记",
        "npc_type": "cadre", "group": "乡镇党委",
        "trust_to_player": 60, "attitude_score": 50,
        "anxiety_level": 50, "granovetter_threshold": 50
      }
    ],
    "npc_relationships": [
      {
        "from_npc_id": "cadre_01", "to_npc_id": "cadre_02",
        "relation_type": "上下级", "strength": 70,
        "description": "直接汇报关系"
      }
    ],
    "decision_points": [
      {
        "decision_id": "DP01", "title": "名单疑云",
        "day_window": "第2-3天",
        "situation": "当前困境描述",
        "is_critical": false,
        "options": [
          {
            "option_id": "DP01_O1", "label": "默认通过",
            "description": "具体做法",
            "cost_action_points": 1, "budget_cost": 0,
            "payoffs": {"global": {"political_credit": -5}},
            "risks": ["可能引发上访"]
          }
        ],
        "affected_npc_ids": ["cadre_02", "villager_05"]
      }
    ],
    "endings": [
      {
        "ending_id": "END_GOOD", "title": "安居乐业",
        "description": "结局叙述",
        "conditions": ["signed_households >= 34", "social_stability_index >= 60"],
        "ending_type": "good"
      }
    ]
  },
  "contexts_used": [],
  "rewritten_queries": ["生态搬迁 政策执行"],
  "generation_notes": ["通过分阶段流水线生成完整结构化初稿。"],
  "generation_mode": "full"
}
```

### 关系类型说明

| 类型 | 含义 | 示例 |
|------|------|------|
| 亲属 | 血缘或姻亲 | 父子、夫妻、兄弟 |
| 上下级 | 组织内的汇报/指令 | 书记→村主任、局长→科长 |
| 利益同盟 | 共同利益驱动 | 承包商与村干部的利益交换 |
| 矛盾对立 | 利益冲突或历史恩怨 | 钉子户与拆迁队、上访户与信访办 |
| 信息渠道 | 消息传递依赖 | 村民中的"消息灵通者" |
| 情感纽带 | 非利益情感关系 | 恩情、友情、同学 |

### 结局类型

| 类型 | 含义 |
|------|------|
| `good` | 好结局：达成核心目标且保持社会稳定 |
| `neutral` | 中性结局：完成任务但留下隐患 |
| `bad` | 坏结局：不同失败路径（资金断裂、民怨爆发、问责等） |

## 目录结构

```text
serious_game/
├── frontend/
│   └── index.html              # 单文件 Web 前端（零构建）
├── src/
│   ├── api/
│   │   └── server.py           # FastAPI 服务端 + SSE
│   ├── config.py               # 配置加载（从 .env）
│   ├── domain/
│   │   ├── act_structure.py    # ActStructure（三幕）
│   │   ├── decision_point.py   # DecisionPoint + DecisionOption
│   │   ├── ending_condition.py # EndingCondition（多结局）
│   │   ├── game_action.py      # GameActionRule + ActionResult
│   │   ├── game_state.py       # GameState（含 budget_unit）
│   │   ├── npc_relationship.py # NPCRelationship（关系网）
│   │   ├── npc_state.py        # NPCState
│   │   └── script_design.py    # ScriptDesign + ScriptGenerationRequest
│   ├── generation/
│   │   ├── script_generator.py          # QwenScriptGenerator（7 阶段生成）
│   │   ├── pa_backend_script_client.py  # PA Backend Agent 适配器
│   │   ├── qwen_client.py               # Qwen OpenAI SDK 客户端
│   │   └── ...                          # 检索、NPC Agent 等
│   └── services/
│       ├── script_gen_service.py # 生成编排
│       └── script_validator.py   # 结构校验
├── run_server.py               # Web 服务入口
├── run_script_generation.py    # CLI 入口
├── requirements.txt
└── .env.example
```

## 常见问题

| 问题 | 排查方向 |
|------|----------|
| Qwen API 401 | 检查 `.env` 中 `DASHSCOPE_API_KEY` |
| PA Backend 登录失败 | 检查 `PA_BACKEND_ACCOUNT` / `PA_BACKEND_PASSWORD` |
| 生成被取消 | 正常行为：点了取消按钮或关闭了页面 |
| 剧本校验失败 | 模型返回了不完整或重复的结构，重新生成或使用修订 |
| 404 Not Found (favicon) | 正常：浏览器尝试获取图标，不影响功能 |
