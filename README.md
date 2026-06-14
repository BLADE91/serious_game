# Serious Game Backend

《父母官》严肃游戏剧本与 NPC Agent 生成原型。输入剧本需求后，系统会检索 OpenSearch 资料，并通过 Qwen 生成可供规则模块继续处理的结构化 JSON。

## 当前能力

- 剧本生成器：支持快速精炼稿和分阶段完整初稿。
- 人类反馈：支持检索前调整 query，以及基于已保存 JSON 进行多轮修订。
- NPC Agent 生成器：基于检索资料生成结构化 NPC Agent 配置，并在设定文本中标注参考文档。
- OpenSearch 检索：支持多 query、初始 query 改写、多步检索、按 `identifier` 读取更多 chunk。
- 结果校验：检查重复 ID、资料引用、关键数值和完整初稿最低规模。
- 共享规则接口：提供 `GameState`、`NPCState`、`GameActionRule`、`SimulationLog`、`ActionResult` 等 dataclass，供后续规则模块复用。

## 目录结构

```text
serious_game/
├── src/
│   ├── config.py
│   ├── domain/
│   │   ├── game_state.py
│   │   ├── npc_state.py
│   │   ├── game_action.py
│   │   ├── simulation_log.py
│   │   ├── script_design.py
│   │   ├── npc_agent.py
│   │   └── source_context.py
│   ├── generation/
│   │   ├── script_generator.py
│   │   ├── qwen_npc_agent_generator.py
│   │   ├── retrieval_planner.py
│   │   ├── opensearch_agent_context_provider.py
│   │   └── opensearch_client.py
│   ├── services/
│   │   ├── script_gen_service.py
│   │   ├── script_validator.py
│   │   └── npc_agent_service.py
│   └── persistence/
│       └── npc_agent_repository.py
├── docs/
│   ├── task_daytime_action_module.md
│   └── task_event_and_night_simulation_module.md
├── run_script_generation.py
├── run_agent_generation.py
├── requirements.txt
└── .env.example
```

## 环境准备

安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

需要 Python 3.10 或更高版本。

创建本地配置：

```bash
cp .env.example .env
```

最小 `.env` 配置：

```env
DASHSCOPE_API_KEY=your_dashscope_api_key_here
QWEN_MODEL=qwen-plus

OPENSEARCH_HOST=your_opensearch_host
OPENSEARCH_PORT=9200
OPENSEARCH_USERNAME=admin
OPENSEARCH_PASSWORD=your_opensearch_password_here
OPENSEARCH_INDEX=serious_game_sources
```

`.env` 会覆盖 shell 中已有的同名环境变量，便于本地调试。`.env` 已被 `.gitignore` 忽略，不要提交真实密钥。

## 运行剧本生成器

快速生成《父母官》方向的精炼版结构化初稿：

```bash
python run_script_generation.py
```

交付用完整初稿采用 7 个短阶段生成：总体骨架、NPC、两批行动规则和三段 90 天事件线。Qwen 通过 OpenAI Python SDK 调用，模块结果由 Python 合并：

```bash
python run_script_generation.py --full-draft
```

人工指定检索 query，跳过自动改写：

```bash
python run_script_generation.py --queries "生态搬迁 基层治理,征地补偿 信访,压力型体制 基层干部"
```

检索前人工确认或替换 query：

```bash
python run_script_generation.py --review-queries
```

带人工反馈生成：

```bash
python run_script_generation.py --feedback "行动规则要更强调约束、风险和payoff，NPC数量先控制在5个以内"
```

基于已保存的剧本继续多轮修订。修订会复用旧稿中的资料，不会再次检索 OpenSearch，并保存为新文件：

```bash
python run_script_generation.py --revise outputs/script_drafts/script_draft_20260603_161931.json --feedback "把事件冲突提前，并降低初始预算"
```

生成结果会保存到：

```text
outputs/script_drafts/
```

新生成的结果 JSON 包含：

- `script`：剧本主体，包括初始状态、NPC、行动规则、事件、夜间规则和引用。
- `contexts_used`：本次生成实际使用的检索资料。
- `rewritten_queries`：检索使用的改写 query。
- `original_query`、`feedback`：原始需求和本轮人工反馈。
- `generation_mode`、`revision_round`：生成模式和修订轮次。

仓库中的 `outputs/script_drafts/script_draft_20260603_161931.json` 是精炼模式示例。

## 常见问题

- `Qwen API HTTP 401`：检查 `.env` 中的 `DASHSCOPE_API_KEY`。程序会优先使用 `.env`。
- `Qwen API request timed out`：完整模式单轮输出较长，程序会自动重试，仍失败时可提高 `QWEN_TIMEOUT_SECONDS`。
- `OpenSearch 连接或查询失败`：检查主机、端口、账号、密码、索引名和网络访问权限。
- `剧本结构校验失败`：模型结果存在重复 ID、未知资料引用、非法数值，或完整稿未达到最低规模。根据错误提示重新生成或使用 `--feedback` 修订。

## 运行 NPC Agent 生成器

只测试单次 OpenSearch 检索：

```bash
python run_agent_generation.py
```

运行多步检索：

```bash
python run_agent_generation.py --iterative
```

运行完整链路：

```bash
python run_agent_generation.py --full
```
