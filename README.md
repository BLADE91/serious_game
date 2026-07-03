# 父母官严肃游戏剧本生成器

本项目生成基层治理题材的严肃游戏剧本。当前主流程以 Markdown 为创作源，先分阶段生成全局设定、大纲和章节正文，再自动修复有明确双重证据的事实连续性问题，最后完成结构化抽取和确定性校验。Web 与 CLI 共用同一套章节式生成服务。

## 当前工作流

| 阶段 | 执行方 | 主要产物 | 作用 |
|---|---|---|---|
| Call 1 | PA Backend | `01_game_settings.md` | 全局变量、NPC、结局和创作约束 |
| Call 2 | PA Backend | `02_chapter_outline.md` | 章节大纲、Flag 规划和结局可达性 |
| Call 3 | PA Backend，逐章 | `03_chNN.md` | 章节正文、情报、决策、结果和结算 |
| Call 4 | Qwen Flash + 程序 | `05_continuity_review.json` | 审查事实矛盾，程序只应用唯一匹配的定点补丁并复审 |
| 合并 | 程序 | `04_merged.md` | 合并修复后的全部 Markdown 创作源 |
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
# Call 4、Call 5 和受影响章节同步修订
DASHSCOPE_API_KEY=your_dashscope_api_key

# Call 1、Call 2、Call 3 和根据用户意见生成修订候选
PA_BACKEND_BASE_URL=https://apitest.know-pa.cn
PA_BACKEND_ACCOUNT=your_account
PA_BACKEND_PASSWORD=your_password
PA_BACKEND_COLLECTION_ID=your_collection_id
PA_BACKEND_SUPABASE_URL=https://your-project.supabase.co
PA_BACKEND_SUPABASE_KEY=your_key
```

`PA_BACKEND_COLLECTION_ID` 会作为 `collection_ids` 约束知识库检索；章节创作同时保持网络搜索开启，因此网络来源不受该 collection 限制。服务首次请求时会在终端打印实际生效的 collection 列表。

PA Backend 阶段返回空内容时会更换会话并退避重试，默认最多重试 2 次。可通过 `PA_BACKEND_MAX_STAGE_RETRIES` 和 `PA_BACKEND_RETRY_BACKOFF_SECONDS` 调整。

`SCRIPT_GENERATION_BACKEND` 仍用于旧的非章节式 CLI 流程，不改变上述章节式 Call 分工。

## Web 使用

```bash
python run_server.py --reload
```

打开 `http://localhost:8000`。Web 的生成按钮直接运行章节式主流程，结果页可查看概览、剧情分叉树、NPC、结局和 Markdown。

NPC、章节、结局以及每章决策点数量均由用户输入。每章决策点数量会进入 Call 1、Call 2 和 Call 3，并在 Call 6 检查实际抽取数量是否一致。

“一键生成”始终创建新版本；旁边的“续跑”按钮会列出全部未完成版本供用户选择，并复用已有阶段产物。新版本会保存原始生成参数，续跑时不会被当前表单中的新值覆盖。

章节源文件修订支持一次编辑多个文件：

- **批量草稿**：全局设定、大纲和各章可全选并切换编辑，切换文件不会触发重建。
- **直接编辑**：在元素旁点击“编辑”时，只把该 ID 对应的修改写入当前批量草稿。
- **AI 候选**：PA Backend 根据当前草稿和意见生成候选，确认后仍只写入草稿。
- **统一提交**：点击“提交全部修改并重建”后只创建一个 `rNN`，自动同步受影响章节、修复连续性并全量重建 JSON。

“审查并重建”可以直接选择磁盘中仍有完整源 Markdown 的版本，因此使用 Claude 等外部工具替换源文件后，不需要手工删除 merge、final 或 JSON。失败的批量修订保留 `revision_job.json`，再次选择该 `rNN` 时只续跑未完成任务。

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
  --ending-count 3 \
  --decision-point-count 3
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
全局设定或大纲修订默认保留受影响章节并生成 `review_required` 版本；增加 `--revision-sync-affected` 可让 AI 同步修订全部受影响章节。

## 输出与续跑

输出位于 `outputs/script_drafts/vNN/`。`00_generation_request.json` 保存该版本的生成参数。普通生成续跑按文件是否存在决定复用；批量修订和“审查并重建”只复制 Markdown 源，并从零生成全部派生 JSON。

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
| `GET` | `/api/incomplete-versions` | 列出所有可续跑的未完成版本 |
| `POST` | `/api/cancel/{task_id}` | 取消生成 |
| `GET` | `/api/revisions/source` | 读取修订目标 Markdown |
| `GET` | `/api/revisions/sources` | 读取版本的全部源 Markdown |
| `POST` | `/api/revisions/preview` | 生成手工 diff 或 AI 候选 |
| `POST` | `/api/revisions/impact` | 分析全局设定或大纲修订的章节影响 |
| `POST` | `/api/revisions/apply` | 创建修订版本并重建产物 |
| `POST` | `/api/revisions/batch-apply` | 批量提交源文件并全量重建 |
| `POST` | `/api/revisions/batch-resume` | 续跑失败的批量修订任务 |
| `GET` | `/api/source-versions` | 列出可从 Markdown 重建的版本 |
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
- `src/services/revision_impact_analyzer.py`：全局设定和大纲的确定性影响分析
- `src/api/server.py`：Web API 和版本持久化
- `frontend/index.html`：Web 操作界面
- `run_script_generation.py`：CLI 入口
