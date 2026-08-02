# 任务：事件与夜间推演模块

这阶段先不做前端，也不要接 Qwen、OpenSearch 或数据库。你的任务是实现白天结束后系统自动发生的事情。

你会用到 `src/domain/` 里的共享数据结构，比如 `GameState`、`NPCState`、`SimulationLog`。

这些共享数据结构先不要改。如果觉得字段不够，先把临时信息放进 `metadata`，然后反馈给主线统一调整。

## 你负责什么

你负责实现事件检查和夜间推演。

建议新建文件：

- `src/services/night_simulation_service.py`
- `src/services/event_service.py`

先实现这些规则：

- 每 7 天生成一次督查周报。
- `social_stability_index` 低于 50 时生成集体行动预警。
- `budget_remaining` 低于 0 时生成超支风险。
- 签约率超过某个 NPC 的 `granovetter_threshold` 时，让这个 NPC 的态度发生变化。
- 每天夜里生成一条玩家第二天能看到的夜间摘要。

Granovetter 阈值规则先做简化版：

- 如果当前签约率超过 NPC 的阈值，并且他的核心诉求已满足，`attitude_score` 增加 20。
- 如果当前签约率超过 NPC 的阈值，但核心诉求还没满足，`attitude_score` 增加 5。

## 最小验收标准

- 能输入一个 `GameState`、一组 `NPCState` 和一些日志。
- 运行夜间推演后，部分 NPC 态度发生变化。
- 产生一条夜间摘要。
- 第 7 天能产生督查周报。
- SSI 低于 50 时能产生集体行动预警。

## 不需要做什么

你不用处理玩家白天具体做了什么，也不用实现行动点扣除。这些是另一个模块。

你也不要调用 Qwen、OpenSearch、数据库或前端。

## 合并方式

你的模块先不要调用白天行动模块。

后面主线会统一把流程串起来：

白天行动 -> 事件检查 -> 夜间推演 -> 第二天
