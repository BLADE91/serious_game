# Serious Game Backend

《父母官》严肃游戏后端原型。当前重点是把“剧本生成器 + NPC Agent 生成器 + 可复用规则接口”先跑通，为后续游戏规则模块和前端 Demo 提供结构化输入。

## 当前能力

- 剧本生成器：从简单需求出发，改写检索 query，检索 OpenSearch 资料，再用 Qwen 生成结构化剧本初稿。
- NPC Agent 生成器：基于检索资料生成结构化 NPC Agent 配置，并在设定文本中标注参考文档。
- OpenSearch 检索：支持多 query、初始 query 改写、多步检索、按 `identifier` 读取更多 chunk。
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
│   │   └── npc_agent_service.py
│   └── persistence/
│       └── npc_agent_repository.py
├── docs/
│   └── vibe_coder_contract.md
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

创建本地配置：

```bash
cp .env.example .env
```

最小 `.env` 配置：

```env
DASHSCOPE_API_KEY=your_dashscope_api_key_here
QWEN_MODEL=qwen-plus

OPENSEARCH_HOST=localhost
OPENSEARCH_PORT=9200
OPENSEARCH_USERNAME=admin
OPENSEARCH_PASSWORD=your_opensearch_password_here
OPENSEARCH_INDEX=serious_game_sources
```

`.env` 会覆盖 shell 中已有的同名环境变量，便于本地调试。`.env` 已被 `.gitignore` 忽略，不要提交真实密钥。

## 运行剧本生成器

默认生成《父母官》方向的小规模结构化剧本初稿：

```bash
python run_script_generation.py
```

人工指定检索 query，跳过自动改写：

```bash
python run_script_generation.py --queries "生态搬迁 基层治理,征地补偿 信访,压力型体制 基层干部"
```

生成结果会保存到：

```text
outputs/script_drafts/
```

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
