# Serious Game Backend

一个用于剧情向游戏后端的 Python 项目，主要负责：

- 剧情节点管理与分支选择
- NPC Agent 配置与行为逻辑
- 游戏状态保存与恢复
- 剧本生成与 NPC 生成

---

## 目录结构

建议采用以下目录结构：
```text
serious_game/
├── src/
│   ├── api/
│   │   ├── routes.py
│   │   └── middleware.py
│   ├── controllers/
│   │   ├── story_controller.py
│   │   ├── npc_controller.py
│   │   └── state_controller.py
│   ├── services/
│   │   ├── story_service.py
│   │   ├── npc_agent_service.py
│   │   ├── script_gen_service.py
│   │   └── state_service.py
│   ├── domain/
│   │   ├── story_node.py
│   │   ├── choice.py
│   │   ├── story_graph.py
│   │   └── story_state.py
│   │   ├── npc_profile.py
│   │   ├── npc_behavior.py
│   │   └── npc_dialogue.py
│   ├── models/
│   │   ├── game_session.py
│   │   ├── player_state.py
│   │   └── npc_model.py
│   ├── persistence/
│   │   ├── db.py
│   │   ├── story_repository.py
│   │   └── state_repository.py
│   ├── generation/
│   │   ├── script_generator.py
│   │   └── npc_agent_generator.py
│   ├── config.py
│   └── utils.py
├── tests/
├── docs/
├── .env.example
├── requirements.txt
└── README.md
```

---

## 功能说明

- `src/api/`：提供 HTTP 接口
- `src/controllers/`：接收请求并调用服务
- `src/services/`：实现剧情推进、NPC 决策、状态更新逻辑
- `src/domain/`：定义剧情节点、分支、NPC 信息、游戏状态等核心模型
- `src/persistence/`：持久化存储逻辑，支持内存、文件、数据库等
- `src/generation/`：剧本生成与 NPC 生成模块