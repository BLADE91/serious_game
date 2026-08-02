# 《浊流之下·清江搬迁记》严肃游戏

本仓库保存基层治理行为模拟严肃游戏的权威剧本、产品资料、开发规格和可运行代码。仓库不再包含旧剧本生成器；当前可运行产品位于 `code/`。

## 当前状态

- M0–M3 文字游戏运行时已经完成，可从 D1 推进至 D90。
- optimization-0.5 已补齐日常玩法闭环、政策工具、知识上下文、多人多轮会谈、D75 补签规则和 `pkg_gameplay_v2` 剧本包。
- M4 研究治理代码基线已完成，正式制度文本、生产资源与验收仍待补齐。
- 当前玩家前端是终端文字客户端；美术化前端尚未进入本仓库。

详细进度见 [游戏产品代码里程碑](code/MILESTONES.md)。

## 权威资料

| 内容 | 位置 | 说明 |
|---|---|---|
| 最终剧本 | [最终剧本.md](最终剧本.md) | 人物、剧情、固定决策、硬结算与结局条件的内容权威 |
| 结构化剧本包 | `code/backend/content/packages/pkg_gameplay_v2/` | optimization-0.5 当前游戏运行内容 |
| 开发补充规格 | `docs/development/specs/` | 数值、旗标、状态初值、动作与行动点规则 |
| 内容与玩法优化 | `docs/game_design/optimization/` | 已确认的优化口径与 D75 冲突定稿 |
| 产品与技术资料 | `docs/product/` | 产品需求、技术设计、测试计划和美术需求 |
| 项目材料 | `docs/project_materials/` | 项目提案、申请书、研究汇报等材料 |

完整文档导航见 [docs/README.md](docs/README.md)。

## 代码结构

```text
code/
├── backend/             # FastAPI 权威后端、剧本包、迁移、测试
└── frontend/terminal/   # 只消费玩家 API 的文字客户端
```

后端负责游戏硬状态、剧情时钟、行动结算、NPC 会谈边界、存档和研究治理。LLM 只生成角色台词与受约束的软状态候选，不直接修改硬结算、旗标或结局。

## 本地运行

需要 Python 3.10+。先启动后端：

```bash
cd code/backend
python -m pip install -r requirements-dev.txt
PYTHONPATH=src python run_server.py
```

再启动终端客户端：

```bash
cd code/frontend/terminal
python main.py
```

后端默认监听 `http://localhost:8100`，接口文档位于 `http://localhost:8100/docs`。环境变量、SQLite/MySQL 和真实 LLM 配置见 [后端说明](code/backend/README.md)。

## 测试

```bash
cd code/backend
PYTHONPATH=src python -m pytest

cd ../frontend/terminal
python -m pytest
```

更完整的协议和验收记录位于 `code/backend/docs/`。
