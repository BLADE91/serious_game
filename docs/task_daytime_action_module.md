# 任务：白天行动模块

这阶段先不做前端，也不要接 Qwen、OpenSearch 或数据库。你的任务是实现玩家白天能做的行动，把基础规则先跑起来。

你会用到 `src/domain/` 里的共享数据结构，比如 `GameState`、`NPCState`、`SimulationLog`、`GameActionRule`、`ActionResult`。

这些共享数据结构先不要改。如果觉得字段不够，先把临时信息放进 `metadata` 或 `direct_payoff`，然后反馈给主线统一调整。

## 你负责什么

你负责实现玩家白天能做的行动。

建议新建文件：`src/services/action_service.py`。

先实现这几个基础行动：

- 入户走访
- 干部私谈
- 调阅档案
- 提高补偿
- 公开承诺

每个行动至少要处理：

- 行动点消耗
- 预算变化
- 目标 NPC 状态变化
- 风险提示
- 日志输出

## 最小验收标准

- 能创建一个 `GameState` 和几个 `NPCState`。
- 执行一次“入户走访 杨德清”。
- 行动点减少 1。
- 杨德清的 `trust_to_player` 增加 5。
- 输出一条 `SimulationLog`。

## 不需要做什么

你不用处理夜间互动、事件触发、NPC 之间的信息传播。这些是另一个模块。

你也不要调用 Qwen、OpenSearch、数据库或前端。

## 合并方式

你的模块先不要调用夜间推演模块。

后面主线会统一把流程串起来：

白天行动 -> 事件检查 -> 夜间推演 -> 第二天
