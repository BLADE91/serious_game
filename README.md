# 父母官严肃游戏剧本生成器

本项目生成基层治理题材的严肃游戏剧本。当前主流程以 Markdown 为创作源，先分阶段生成全局设定、大纲和章节正文，再做事实连续性审查、结构化抽取和确定性校验。Web 与 CLI 共用同一套章节式生成服务。

## 当前工作流

| 阶段 | 执行方 | 主要产物 | 作用 |
|---|---|---|---|
| Call 1 | PA Backend | `01_game_settings.md` | 全局变量、NPC、结局和创作约束 |
| Call 2 | PA Backend | `02_chapter_outline.md` | 章节大纲、Flag 规划和结局可达性 |
| Call 3 | PA Backend，逐章 | `03_chNN.md` | 章节正文、情报、决策、结果和结算 |
| 合并 | 程序 | `04_merged_before_review.md` | 合并全部 Markdown 创作源 |
| Call 4 | Qwen Flash | `05_continuity_review.json` | 仅报告有两处原文证据的事实矛盾，不自动改稿 |
| Call 5a | Qwen Flash | `06a_global.json` | 抽取全局结构、NPC 和结局 |
| Call 5b | Qwen Flash，逐章 | `06b_chNN.json` | 抽取各章剧情树 |
| Call 5 合并 | 程序 | `06_script_structure.json` | 合并结构化结果 |
| Call 6 | 程序 | `07_validation_report.json` | 校验必要结构和 Markdown 到 JSON 的 ID 提取完整性 |

自动校验只负责故事事实连续性、必要结构和提取是否有效。策略张力、教学反馈、人物行为合理性和叙事质量由人工评审，不作为自动阻断条件。

## 环境准备

需要 Python 3.10+：

```bash
pip install -r requirements.txt
cp .env.example .env
```

章节式主流程同时需要两组配置：

```env
# Call 4、Call 5 和 AI 修订
DASHSCOPE_API_KEY=your_dashscope_api_key

# Call 1、Call 2、Call 3
PA_BACKEND_BASE_URL=https://apitest.know-pa.cn
PA_BACKEND_ACCOUNT=your_account
PA_BACKEND_PASSWORD=your_password
PA_BACKEND_SUPABASE_URL=https://your-project.supabase.co
PA_BACKEND_SUPABASE_KEY=your_key
```

`SCRIPT_GENERATION_BACKEND` 仍用于旧的非章节式 CLI 流程，不改变上述章节式 Call 分工。

## Web 使用

```bash
python run_server.py --reload
```

打开 `http://localhost:8000`。Web 的生成按钮直接运行章节式主流程，结果页可查看概览、剧情分叉树、NPC、结局和 Markdown。

人工修订有两种方式：

- **直接编辑**：在元素旁点击“编辑”，浮层只编辑该 ID 对应的 Markdown 内容块。
- **AI 修订**：在元素旁点击“AI 修订”，先生成候选和 diff，确认后再应用。

应用修订不会覆盖原版，而是在 `vNN/revisions/rNN/` 创建完整修订版本，并重新执行受影响的抽取和校验步骤。

API 文档：`http://localhost:8000/docs`

## CLI 使用

全新生成：

```bash
python run_script_generation.py \
  --chapter \
  --scenario "生态搬迁" \
  --player-role "乡镇党委副书记" \
  --learning-goal "体验基层政策执行中的多重压力" \
  --duration 45 \
  --chapter-count 4 \
  --ending-count 3
```

直接用完整 Markdown 文件替换一个修订目标：

```bash
python run_script_generation.py \
  --chapter-revise v01 \
  --revision-target ch02 \
  --revision-file /path/to/ch02.md
```

生成 AI 修订候选并应用；增加 `--revision-preview-only` 可只看 diff：

```bash
python run_script_generation.py \
  --chapter-revise v01 \
  --revision-target ch02 \
  --feedback "只调整 ch02_choice_01，补强上一章选择后果的承接"
```

修订目标只接受 `game_settings`、`chapter_outline` 或 `chNN`。

## 输出与续跑

输出位于 `outputs/script_drafts/vNN/`。生成器按文件是否存在决定复用：已有文件跳过，缺失文件重跑。它不会计算内容 hash，也不会因上游文件被手动修改而自动删除下游缓存。

需要重跑抽取时，至少删除 `06_script_structure.json` 和目标 `06a_global.json` 或 `06b_chNN.json`；需要重跑校验时删除 `07_validation_report.json`。更完整的文件关系和修订目录说明见 [剧本草稿存储说明](outputs/script_drafts/剧本草稿存储说明.md)。

新版本号按 `.version_counter` 和已有 `vNN` 目录中的较大值递增，计数器过期不会覆盖现有版本目录。

## 结果结构

最终 JSON 顶层包含 `script`、`full_md`、`generation_mode`、`generation_notes` 和版本元信息。章节式核心结构如下：

```json
{
  "script": {
    "title": "剧本标题",
    "chapter_npcs": [],
    "chapters": [
      {
        "chapter_id": "ch01",
        "info_nodes": [],
        "decision_points": [
          {
            "node_id": "ch01_choice_01",
            "options": [
              {
                "choice_id": "ch01_choice_01_A",
                "effects": {"signed": 3, "public_trust": -5},
                "flags_added": []
              }
            ]
          }
        ],
        "results": [],
        "checkpoint": {},
        "state_snapshot": {}
      }
    ],
    "chapter_endings": [],
    "validation_report": {
      "valid": true,
      "structure_validation": {},
      "extraction_validation": {},
      "continuity_review": {}
    }
  },
  "full_md": "# 完整剧本...",
  "generation_mode": "chapter"
}
```

系统变量固定为 `signed`、`social_stability`、`political_credit`、`public_trust`、`env_clue`、`media_pressure`、`budget` 和 `days_left`。Flag 用于记录离散选择后果、控制后续内容解锁和结局条件。

## 主要接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/generate` | SSE 运行章节式生成 |
| `POST` | `/api/cancel/{task_id}` | 取消生成 |
| `GET` | `/api/revisions/source` | 读取修订目标 Markdown |
| `POST` | `/api/revisions/preview` | 生成手工 diff 或 AI 候选 |
| `POST` | `/api/revisions/apply` | 创建修订版本并重建产物 |
| `GET` | `/api/versions` | 列出生成版和修订版 |
| `GET` | `/api/version/{filename}` | 读取指定结果 |
| `GET` | `/api/latest-result` | 读取最近结果 |

## 测试

```bash
python -m unittest discover -s tests
```

## 代码位置

- `src/generation/chapter_script_generator.py`：章节式生成、抽取、续跑和校验编排
- `src/generation/chapter_validator.py`：确定性结构与提取校验
- `src/services/chapter_revision_service.py`：人工及 AI 修订版本管线
- `src/api/server.py`：Web API 和版本持久化
- `frontend/index.html`：Web 操作界面
- `run_script_generation.py`：CLI 入口
