# M2 终端文字协议

版本：`v0.3`
适用范围：M1 D1–D3 教程切片及 M2 D1–D90 完整文字运行时

## 1. 协议边界

- 后端是游戏状态、剧本结算、时间推进和幂等性的唯一权威来源。
- 客户端只展示玩家可见 DTO，不读取数据库、剧本包或内部领域对象。
- 默认本地配置使用 SQLite Cookie 账号；只有显式设置 `AUTH_REQUIRED=false` 时才使用 `X-Account-ID` 兼容沙盒身份。生产环境禁止沙盒身份和开放式自助注册。
- 所有 session 接口都校验 `session.account_id == 当前账号`。无权访问时统一返回 `404`，不泄露 session 是否存在。
- 玩家响应不得出现精确信任度、`integrity`、`env_clue`、`corruption_evidence`、内部 flags、LLM 评估或精确暗档 delta。

## 2. 通用约定

基础地址默认为 `http://127.0.0.1:8100`，请求与响应均为 UTF-8 JSON。

关闭认证时的兼容开发请求头：

```http
Accept: application/json
Content-Type: application/json
X-Account-ID: terminal-local
```

每次写操作都携带：

- `client_action_id`：客户端生成的唯一幂等键；新开一局使用同等语义的 `client_request_id`。
- `state_version`：客户端最后看到的状态版本。版本过期返回 `STATE_VERSION_CONFLICT`。

同一幂等键重复提交同一请求会返回第一次的结果；携带不同请求体会返回 `IDEMPOTENCY_KEY_REUSED`。处理中操作可通过操作查询接口轮询。可重试失败必须复用原请求并显式设置 `retry=true`。

启用本地认证时先调用 `POST /api/auth/register` 或 `POST /api/auth/login`。服务端返回 HttpOnly Cookie 和响应体中的 CSRF Token；后续所有写请求携带 `X-CSRF-Token`，读取请求只需 Cookie。密码最少 12 个字符并以 scrypt 随机盐哈希保存。

## 3. 接口清单

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/api/auth/register` | 仅在非生产环境显式开放时注册并自动登录 |
| `POST` | `/api/auth/login` | 登录并取得 Cookie 与 CSRF Token |
| `POST` | `/api/auth/logout` | 注销当前会话 |
| `GET` | `/api/auth/me` | 查看当前账号与角色 |
| `GET` | `/api/game/origins` | 获取五种开局出身 |
| `POST` | `/api/game/session` | 幂等创建新局 |
| `GET` | `/api/game/session/latest-active` | 获取当前账号最近的活动存档 |
| `GET` | `/api/game/session/{session_id}` | 获取玩家可见状态 |
| `GET` | `/api/game/session/{session_id}/view?after=N` | 获取状态、增量文字流和可用命令；终端主入口 |
| `GET` | `/api/game/session/{session_id}/feed?after=N` | 仅获取增量文字流 |
| `GET` | `/api/game/session/{session_id}/actions` | 获取自主行动目录及当前可用性 |
| `GET` | `/api/game/session/{session_id}/opportunities` | 获取可进行自由交谈的 NPC 机会 |
| `GET` | `/api/game/session/{session_id}/knowledge` | 获取玩家已经掌握的事实、线索和证据 |
| `GET` | `/api/game/session/{session_id}/map` | 获取当前文字地图入口及锁定、已知、可用、事件状态 |
| `GET` | `/api/game/session/{session_id}/review` | 获取玩家可见的只读复盘，不返回暗档和未见内容 |
| `GET` | `/api/game/package/validation` | 获取当前发布剧本包的结构与来源校验报告 |
| `POST` | `/api/game/session/{session_id}/action` | 提交决策、自主行动或自由文字互动 |
| `POST` | `/api/game/session/{session_id}/end-day` | 执行夜间模拟并推进日期 |
| `GET` | `/api/game/session/{session_id}/operations/{client_action_id}` | 查询处理中或已完成的幂等操作 |

## 4. 新局与终端视图

创建新局：

```json
{
  "client_request_id": "cli-new-6caecce328554e64933e5423b761cc52",
  "origin_id": "technical"
}
```

`origin_id` 必须来自 `/api/game/origins`，当前固定为 `technical`、`grassroots`、`integrity`、`parachute`、`young` 之一。D1 开场会按所选出身投影条件叙事；玩家可见状态保留出身 ID 和名称。

创建成功返回玩家可见状态，HTTP 状态为 `201`。终端随后调用：

```http
GET /api/game/session/{session_id}/view?after=0
```

终端视图的稳定外形为：

```json
{
  "state": {
    "session_id": "game_...",
    "state_version": 1,
    "status": "active",
    "story": {
      "day": 1,
      "chapter": 1,
      "cost_tier": "normal",
      "beat_id": "beat_d01_arrival_and_reception"
    },
    "ledger": {},
    "indicators": {},
    "pending_decision": {}
  },
  "feed": {
    "after": 0,
    "cursor": 8,
    "items": []
  },
  "commands": {
    "can_choose": true,
    "can_act": false,
    "can_end_day": false,
    "can_talk": false
  }
}
```

`feed.items` 按 `cursor` 严格递增。客户端成功消费后保存响应中的 `feed.cursor`，下次将它作为 `after`，避免重复显示。文字项只包含：

```json
{
  "cursor": 3,
  "story_day": 1,
  "kind": "dialogue",
  "speaker": "郑向东",
  "text": "……"
}
```

`pending_decision.options` 已按权威剧本顺序给出 `option_id + text`。客户端可以临时显示 A/B/C/D，但提交时必须发送 `option_id`，不能把显示序号当作权威标识。

## 5. 动作联合协议

所有动作都提交到同一个 `/action` 接口，`input_mode` 决定互斥字段。

### 5.1 强制决策

```json
{
  "input_mode": "decision",
  "client_action_id": "cli-decision-f202f0e28b074439a0ac6fb9e55c07fb",
  "state_version": 1,
  "decision_id": "ev1_01_reception_bag",
  "option_id": "a_reject_on_site"
}
```

决策固定为 0 行动点。存在 `pending_decision` 时，自主行动、自由文字互动与日终均返回 `DECISION_REQUIRED`。

### 5.2 自主工具行动

```json
{
  "input_mode": "tool",
  "client_action_id": "cli-tool-2d347a1a6b18467793c108af5f577d14",
  "state_version": 7,
  "opportunity_id": "opp_d07_zhou_dashan_home_visit",
  "action_id": "home_visit"
}
```

客户端必须先读取 `/actions`，使用服务端返回的 `available`、`unavailable_reason`、`opportunity_ids` 和实际行动点成本。`opportunity_id` 是必填的上下文入口；服务端会再次校验它仍然开放且与 `action_id` 匹配。客户端不能根据本地规则自行判断。

### 5.3 自由文字互动

```json
{
  "input_mode": "free_text",
  "client_action_id": "cli-talk-baa2a05b0e2a43a4aa3de7f4fdf13f06",
  "state_version": 12,
  "opportunity_id": "opp_...",
  "target_npc_id": "npc_...",
  "player_text": "我想先听听你的顾虑。"
}
```

`opportunity_id` 和 `target_npc_id` 必须来自同一次服务端机会列表。首个真实机会为 D2 的 `opp_d02_wu_xiuying_first_talk`。当前由确定性的 Fake LLM 扮演吴秀英；模型只能提出受限的态度/焦虑档位和允许披露的事实，不能直接写数值、旗标或任意事实。

动作成功响应包含 `operation_id`、新 `state_version`、本回合可见叙述和 `visible_state`。客户端仍应调用 `/view?after=上次游标` 获取权威增量剧情流。

## 6. 日终协议

```json
{
  "client_action_id": "cli-end-8955c7be1cfd4710b6995c0034e34a30",
  "state_version": 2,
  "active_rest": false
}
```

后端依次执行当前日夜间块、夜间模拟、固定事件检查、日期推进、次日开场和行动点重置，并只提交一次状态版本。客户端不能直接修改日期或行动点。

## 7. 错误外形

```json
{
  "error": {
    "code": "STATE_VERSION_CONFLICT",
    "message": "状态版本已变化，请刷新后重试",
    "details": {"current_state_version": 3}
  }
}
```

终端必须识别以下核心错误：

| code | 处理 |
|---|---|
| `NOT_FOUND` | 清除当前局或检查账号 |
| `DECISION_REQUIRED` | 刷新视图并显示待决策选项 |
| `ACTION_UNAVAILABLE` | 显示原因并刷新可用命令 |
| `STATE_VERSION_CONFLICT` | 刷新视图，用户确认后重新发起新操作 |
| `SESSION_BUSY` | 短暂等待后刷新；不能并发写同一局 |
| `IDEMPOTENCY_KEY_REUSED` | 客户端错误；生成新键或恢复原请求体 |
| `OPERATION_RETRY_REQUIRED` | 用户确认后用原键、原业务请求和 `retry=true` 重试 |
| `ROLE_LLM_UNAVAILABLE` | 保留操作信息并提示稍后重试 |
| `ROLE_LLM_INVALID_RESPONSE` | 不采用模型输出；按可重试失败处理 |
| `ROLE_LLM_BUDGET_EXCEEDED` | 停止本次模型调用并提示本局预算已达上限 |
| `ROLE_LLM_CONFIGURATION_ERROR` | 检查 API Key、地域、Base URL 和模型权限；不得自动重试 |

## 8. M1 D1–D3 垂直切片验收流

1. 查询五种出身，以 `technical` 创建新局；状态为 D1、8 行动点、`state_version=1`，开场包含技术干部条件叙事。
2. `/view?after=0` 返回到任、郑向东简报、接风宴文本和 EV1-01 四个选项。
3. 未决策前 `can_choose=true`，其余写命令为 false。
4. 提交任一合法选项，版本变为 2，行动点仍为 8；硬结算只进入内部状态，不泄露精确暗档 delta。
5. 提交日终进入 D2，出现 DP1-01；选择任一工作组路线后出现吴秀英会面入口。
6. 在 `opp_d02_wu_xiuying_first_talk` 提交玩家文字。Fake LLM 返回角色台词，规则层只应用有界态度/焦虑变化，并登记允许的事实与完成旗标。
7. 互动完成前 D2 日终必须被阻塞；完成后日终进入 D3，出现“派系图成形”阶段小结。
8. 处理 D3 的章节决策队列后，`/opportunities` 开放 `opp_d03_zhou_dashan_first_talk`，证明“玩家文字—角色回复—受限状态变化—新机会”闭环成立。
9. 重启后端并使用同一 SQLite 文件，`continue` 可恢复同一 D3 存档、增量游标所需状态和周大山机会；重复提交已成功的幂等键返回原结果，不重复结算。

## 9. 本地持久化约定

沙盒默认 `GAME_REPOSITORY=sqlite`，路径由 `GAME_DATABASE_PATH` 指定。每个仓储操作使用短事务，存档通过显式 JSON 编解码保存，不序列化可执行对象。`runtime_schema_versions` 记录运行库 schema 版本；session 保存受 `state_version` 和单飞占用字段共同保护。操作预留、最终 session 状态和幂等操作结果在同一 SQLite 事务内提交，任一写入失败会整体回滚。

`GAME_REPOSITORY=memory` 仅用于隔离单元测试。正式部署仍须使用技术设计中的 MySQL 适配器和账号会话体系，不能把本地 SQLite 与 `X-Account-ID` 当作生产安全边界。

## 10. M2 D1–D90 验收流

1. 逐个提交当前 `pending_decision` 的合法 `option_id`，没有待决策时调用日终；后端可跨模拟日推进，但遇到下一决策、事件、可玩日或转场必须停下。
2. D31 触发市委巡察组进驻，D45 触发撤离，D59 独立触发顾克明环保迎检，三者不可合并。
3. D1–D89 每个结束日恰有一条幂等夜间日志；自动跳日不得漏记、重放不得重复。
4. `/map` 返回 8 个注册地点的玩家可见状态；地点是否可用只由当前机会和事件投影。
5. `/review` 只包含已经发生的决策、行动、夜间痕迹、事件、事实和终局，不暴露未触发分支的内部条件或精确隐藏值。
6. D90 固定结转后状态变为终局，返回一个主结局、一个亚结局及适用附加位；结局按 14 条轴顺序首命中，不返回综合评分。
7. `/api/game/package/validation` 必须报告 90 个故事日、76 个运行时决策、14 个事件规则、24 个主结局、95 个亚结局、3 个附加位及 8 个地图地点，并验证母稿 SHA-256 与发布包内容哈希。
