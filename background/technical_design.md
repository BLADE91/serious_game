# 严肃游戏技术设计文档

## 1. 设计目标

本文档定义严肃游戏运行时系统的技术方案。当前仓库已经具备剧本生成、Markdown 母稿、JSON 抽取、版本保存和前端展示能力。后续设计重点是新增“可玩模拟循环”，并保留剧本持续更新的空间。

系统不把当前初版剧本写死。所有剧情、NPC、事件、行动、结局和数值阈值都通过剧本包或规则表加载。代码只维护稳定的运行时框架。

## 2. 当前代码基础

当前仓库已有以下能力：

- `src/generation/chapter_script_generator.py`：章节式剧本生成管线。
- `src/generation/chapter_validator.py`：结构与抽取校验。
- `src/services/chapter_revision_service.py`：章节源文件修订和重建。
- `src/api/server.py`：剧本生成、版本、修订 API。
- `frontend/index.html`：剧本生成与查看页面。
- `src/domain/game_state.py`：游戏全局状态。
- `src/domain/npc_state.py`：NPC 运行时状态。
- `src/domain/game_action.py`：行动规则与行动结果。
- `src/domain/simulation_log.py`：仿真日志。

当前缺口：

- 没有白天行动服务。
- 没有事件触发服务。
- 没有夜间推演服务。
- 没有游戏 session 存档。
- 没有账号登录系统。
- 没有真正的玩家游戏接口。
- 当前前端是剧本工具，不是游戏界面。
- NPC Agent 生成服务存在雏形，但尚未接入可玩循环。

## 3. 总体架构

```text
剧本 Markdown 母稿
      ↓
结构化抽取 JSON
      ↓
剧本包 Script Package
      ↓
开局锁定版本
      ↓
Game Session
      ↓
白天行动服务 → 事件服务 → 夜间推演服务 → 次日状态
      ↓
NPC 对话服务 / 复盘 / 数据导出
```

运行时分为五层：

1. 内容层：剧本、NPC、事件、规则、结局。
2. 规则层：行动结算、事件触发、夜间传播、结局判定。
3. Agent 层：NPC 对话、记忆、摘要、风格控制。
4. API 层：Session、Action、Dialogue、Save、Replay。
5. 前端层：游戏界面、复盘界面、编辑工具。

第一阶段按本地优先设计，但数据库按正式项目基线处理。开发环境使用本地 MySQL 或 Docker MySQL，服务器部署继续使用 MySQL。部署形态可以先是本机运行 FastAPI，浏览器访问本机地址；后续迁移服务器时，账号、存档、日志和研究数据的边界不需要重做。

## 4. 剧本包设计

### 4.1 目录结构

建议新增：

```text
outputs/script_packages/
  pkg_YYYYMMDD_HHMMSS/
    package_manifest.json
    full_script.md
    script_structure.json
    npc_profiles.json
    action_rules.json
    event_rules.json
    ending_rules.json
    prompt_templates/
      npc_dialogue_system.md
      action_parser_system.md
      night_summary_system.md
    assets_manifest.json
    art/
      brand/
      ui/
      icons/
      npc/
      map/
      scenes/
      events/
      evidence/
      endings/
      tutorial/
      system/
```

### 4.2 package_manifest.json

```json
{
  "package_id": "pkg_20260706_001",
  "title": "父母官",
  "source_version": "v01",
  "created_at": "2026-07-06T00:00:00+08:00",
  "script_schema_version": 1,
  "rules_schema_version": 1,
  "prompt_version": "npc_prompt_v1",
  "model_profile": "dev_default",
  "notes": "初级剧本包，可替换"
}
```

### 4.3 内容加载原则

- 开局时复制剧本包关键内容到 session 元数据。
- 已开局 session 不跟随剧本包变化。
- 新剧本重新抽取后生成新 package。
- 旧 session 仍可读取旧 package。

### 4.4 美术资源包

美术需求详见 `art_requirement.md`。技术侧只依赖该文档规定的资源契约，不直接依赖某张图片的临时文件名。

每个剧本包必须包含：

- `assets_manifest.json`：资源索引和程序读取入口。
- `art/`：游戏运行时资源导出目录。
- `art/ui/design_tokens.json`：前端可读取的颜色、间距、圆角和字体 token。
- `art/map/map_layers.json`：地图可交互元素与 NPC、地点、事件的映射。

禁止事项：

- 禁止在前端或后端硬编码具体图片路径。
- 禁止用 NPC 中文名、地点中文名作为资源主键。
- 禁止让旧 session 自动读取新剧本包中的美术资源。
- 禁止运行时读取 PSD、AI、Figma 源文件。

`assets_manifest.json` 顶层结构：

```json
{
  "schema_version": 1,
  "package_id": "pkg_20260706_001",
  "art_direction_version": "art_v1",
  "base_path": "art/",
  "design_tokens": {
    "file": "art/ui/design_tokens.json"
  },
  "assets": []
}
```

单个资源最小结构：

```json
{
  "asset_id": "npc_yang_deqing_portrait_neutral",
  "category": "npc",
  "type": "portrait",
  "path": "art/npc/npc_yang_deqing/portrait_neutral.webp",
  "fallback_asset_id": "npc_unknown_portrait_neutral",
  "linked_entity_type": "npc",
  "linked_entity_id": "npc_yang_deqing",
  "state": "neutral",
  "usage": ["dialogue", "npc_profile", "review"],
  "format": "webp",
  "width": 512,
  "height": 512,
  "version": "1.0.0",
  "license": "project_internal",
  "commercial_use": true,
  "alt_text": "杨德清普通状态头像"
}
```

后端需要新增 `AssetManifestService`：

```text
src/services/asset_manifest_service.py
```

职责：

- 加载指定 `package_id` 的 `assets_manifest.json`。
- 校验 `asset_id` 唯一。
- 校验 `path` 是否存在。
- 校验 `linked_entity_id` 是否能在 NPC、事件、地点或结局规则中找到。
- 按 `asset_id`、`linked_entity_id`、`usage`、`state` 查询资源。
- 提供 fallback 链。

接口建议：

```python
class AssetManifestService:
    def load_manifest(self, package_id: str) -> AssetManifest:
        ...

    def get_asset(self, package_id: str, asset_id: str) -> AssetRef:
        ...

    def find_assets(
        self,
        package_id: str,
        linked_entity_type: str | None = None,
        linked_entity_id: str | None = None,
        usage: str | None = None,
        state: str | None = None,
    ) -> list[AssetRef]:
        ...
```

建议新增 API：

```text
GET /api/script-packages/{package_id}/assets
GET /api/script-packages/{package_id}/assets/{asset_id}
GET /api/script-packages/{package_id}/art/{path}
```

第三个接口只允许读取当前剧本包 `art/` 目录下已登记资源，禁止任意文件路径读取。

## 5. 领域模型扩展

现有 dataclass 可继续使用。第一阶段优先避免大改共享结构，临时字段放入 `metadata`。

建议逐步新增以下模型：

```text
src/domain/account.py
src/domain/game_session.py
src/domain/event_rule.py
src/domain/ending_rule.py
src/domain/dialogue_message.py
src/domain/player_action.py
src/domain/npc_memory.py
src/domain/script_package.py
```

### 5.1 Account

职责：

- 表示登录账号。
- 保存密码哈希元数据。
- 绑定玩家存档。
- 为后续角色权限预留字段。

核心字段：

```python
account_id: str
username: str
password_hash: str
password_hash_scheme: str
role: str
created_at: str
disabled: bool
metadata: dict
```

第一阶段 `role` 只需要支持 `player` 和 `admin`。权限系统先不展开，但字段必须保留。

密码要求：

- 禁止明文保存。
- 优先使用 `argon2id`。
- 如果运行环境不方便引入 Argon2，使用 `bcrypt`。
- 不使用普通 SHA256、MD5 或可逆加密保存密码。

### 5.2 GameSession

职责：

- 绑定剧本包版本。
- 绑定账号。
- 保存当前状态。
- 保存 NPC 状态集合。
- 保存日志。
- 标记是否结束。

核心字段：

```python
session_id: str
account_id: str
package_id: str
created_at: str
status: str
game_state: GameState
npc_states: dict[str, NPCState]
flags: set[str]
triggered_events: set[str]
logs: list[SimulationLog]
metadata: dict
```

### 5.3 NPCMemory

第一阶段用结构化记忆，不直接上向量库：

```python
npc_id: str
facts_known: list[str]
rumors_heard: list[str]
promises_from_player: list[dict]
recent_dialogue_summary: str
relationship_notes: list[str]
hidden_state: dict
```

后续可把 `facts_known`、`rumors_heard`、对话摘要写入向量检索。

## 6. 服务设计

### 6.1 AccountService

文件：

```text
src/services/account_service.py
```

职责：

- 创建账号。
- 校验用户名和密码。
- 生成密码哈希。
- 验证密码哈希。
- 禁用账号。

接口建议：

```python
class AccountService:
    def create_account(self, username: str, password: str, role: str = "player") -> Account:
        ...

    def authenticate(self, username: str, password: str) -> Account | None:
        ...
```

第一阶段账号创建可以通过管理脚本或管理员接口完成。前端不做自助注册。

### 6.2 AuthSessionService

文件：

```text
src/services/auth_session_service.py
```

职责：

- 登录后创建服务端会话。
- 校验请求中的 session token。
- 注销登录。
- 把 `account_id` 注入游戏接口。

第一阶段可以使用 HttpOnly Cookie 保存随机 session token。token 和账号映射存入数据库。后续服务器部署时再补充过期策略、刷新令牌和 CSRF 防护。

### 6.3 ActionService

文件：

```text
src/services/action_service.py
```

职责：

- 加载行动规则。
- 校验行动点、预算和目标。
- 结算行动点、预算和确定性世界状态影响。
- 生成供 NPC LLM 评估的行动上下文。
- 输出日志。

接口建议：

```python
class ActionService:
    def execute(
        self,
        game_state: GameState,
        npc_states: dict[str, NPCState],
        action_id: str,
        target_npc_id: str | None = None,
        payload: dict | None = None,
    ) -> ActionResult:
        ...
```

第一阶段实现：

- `home_visit`
- `cadre_private_talk`
- `review_archive`
- `raise_compensation`
- `public_commitment`

### 6.4 ActionParserService

职责：

- 辅助识别玩家文字输入中的行动意图、目标 NPC、承诺、金额、话题和风险词。
- 输出候选行动和置信度，供合规检查和日志记录使用。
- 低置信度时要求玩家选择确认。

核心原则：

- 玩家与 NPC 的主要互动形式是“说服式文字输入”，不是固定按钮结算。
- ActionParserService 不决定 NPC 是否被说服，也不决定 NPC 指标变化。
- ActionParserService 不能用固定关键词规则替代 NPC 的态度判断。
- 无论解析出什么行动标签，玩家原始文本都必须完整传入目标 NPC 的 `NPCStateEvaluationService`。
- 快捷行动只用于降低输入成本和补充结构化参数，不能替代自由文字说服。

第一阶段可以用规则关键词做“行动归类”和“合规预筛”，但不能用规则表直接给 NPC 加减信任、态度或签约意愿。

后续可接 LLM 做行动解析，但解析 LLM 也只输出 JSON，不承担 NPC 指标结算：

```json
{
  "action_id": "home_visit",
  "target_npc_id": "villager_001",
  "parameters": {
    "topic": "compensation"
  },
  "confidence": 0.82
}
```

### 6.5 ComplianceService

职责：

- 拒绝非法行动。
- 检查承诺是否超预算。
- 检查是否越权。
- 检查是否违反剧本边界。

设计原则：

- 先规则校验，再 LLM 回复。
- 被拒绝的行动不扣行动点。
- 拒绝日志仍需保存。

### 6.6 NPCStateEvaluationService

文件：

```text
src/services/npc_state_evaluation_service.py
```

职责：

- 将玩家说服文本、行动归类、目标 NPC 档案、NPC 当前状态、NPC 记忆、世界状态、相关事件和承诺组装为 prompt。
- 调用“目标 NPC 自己”的 LLM 评估本次说服是否有效。
- 要求 LLM 按该 NPC 的人设、利益、红线、知识边界、语言风格和当前处境输出固定 JSON。
- 返回该 NPC 自评的指标变化、行为倾向、对玩家态度和风险说明。

接口建议：

```python
class NPCStateEvaluationService:
    def evaluate(
        self,
        game_state: GameState,
        npc_state: NPCState,
        player_text: str,
        action_context: dict,
        memory_context: dict,
        world_context: dict,
    ) -> NPCStateEvaluation:
        ...
```

LLM 输出示例：

```json
{
  "npc_id": "npc_yang_deqing",
  "attitude_delta": {
    "trust_to_player": -6,
    "attitude_score": 3,
    "anxiety_level": 8,
    "reference_point": 0
  },
  "state_flags": {
    "core_demand_satisfied": false,
    "signed_intent": false,
    "will_share_with": ["npc_li_ergu"]
  },
  "reasoning_summary": "玩家没有回应祖坟问题，角色愿意继续谈但信任下降。",
  "dialogue_intent": "继续试探玩家，不直接拒绝",
  "risk_notes": ["祖坟问题未处理"]
}
```

设计原则：

- 目标 NPC 的 LLM 决定该 NPC 对玩家说服文本和行动意图的态度变化。
- 同一句玩家文本传给不同 NPC，可能产生不同指标变化。
- 同一句玩家文本在不同记忆、事件和世界状态下，可能产生不同指标变化。
- 系统规则只负责行动合法性、资源扣除、输出校验、数值裁剪和数据库提交。
- 系统不得用固定数值表替代 NPC LLM 对“是否被说服”的判断。
- LLM 输出必须结构化。
- LLM 不直接写数据库。
- LLM 不直接改变签约、预算、行动点和结局。
- 系统保留 LLM 原始 JSON，供复盘和研究分析。

### 6.7 StateDeltaValidator

文件：

```text
src/services/state_delta_validator.py
```

职责：

- 校验 `NPCStateEvaluationService` 输出。
- 限制单次指标变化范围。
- 检查目标 NPC 是否存在。
- 检查 LLM 是否引用超出 NPC 知识边界的信息。
- 检查 `signed_intent` 是否满足核心诉求、预算和程序条件。
- 对可裁剪的数值进行裁剪。
- 对严重违规输出要求重试或降级。

第一阶段建议阈值：

- 单次 `trust_to_player`、`attitude_score`、`anxiety_level` 变化默认限制在 `-20` 到 `+20`。
- 关键事件允许更大变化，但必须由事件上下文显式授权。
- `signed` 不能由 LLM 直接改为 true。LLM 只能输出 `signed_intent: true`，最终签约由系统检查预算、行动条件和核心诉求后提交。

### 6.8 EventService

文件：

```text
src/services/event_service.py
```

职责：

- 检查固定事件。
- 检查条件事件。
- 检查随机事件。
- 返回待处理事件。

接口建议：

```python
class EventService:
    def check_events(
        self,
        game_state: GameState,
        npc_states: dict[str, NPCState],
        logs: list[SimulationLog],
        triggered_events: set[str],
    ) -> list[TriggeredEvent]:
        ...
```

第一阶段规则：

- 每 7 天督查周报。
- `social_stability_index < 50` 生成集体行动预警。
- `budget_remaining < 0` 生成超支风险。
- D45 触发巡视组。
- D90 触发结局计算。

### 6.9 NightSimulationService

文件：

```text
src/services/night_simulation_service.py
```

职责：

- 执行 NPC 之间的信息传播。
- 执行 Granovetter 阈值变化。
- 生成隐藏日志。
- 生成玩家次日可见摘要。
- 推进到下一天。

接口建议：

```python
class NightSimulationService:
    def run_night(
        self,
        game_state: GameState,
        npc_states: dict[str, NPCState],
        logs: list[SimulationLog],
    ) -> NightSimulationResult:
        ...
```

夜间推演同样可以调用 LLM 做 NPC 自评，但优先控制调用次数。第一阶段可对高影响 NPC 或高焦虑 NPC 调用 `NPCStateEvaluationService`，普通传播仍使用规则。

第一阶段简化规则：

- 签约率、核心诉求满足度和社会网络传播只生成“夜间处境变化”上下文，不直接写入 NPC 信任或态度。
- 高影响或状态临界的 NPC 由 `NPCStateEvaluationService` 根据夜间处境、关系网络和自身人设输出受限指标变化；低影响 NPC 可只更新规则性的传播事实与风险候选，留待下一次角色回合评估。
- 焦虑值高于 70 的 NPC 可能生成隐藏风险日志或夜间评估候选。
- 每晚生成一条可见摘要。

### 6.10 DialogueService

职责：

- 构建 NPC 对话 prompt。
- 注入固定人物档案、当前状态、记忆和最近对话。
- 调用 LLM。
- 对输出做规则校验。

重要约束：

- DialogueService 不直接修改 GameState 或 NPCState。
- NPCState 的变化来自 `NPCStateEvaluationService` 的结构化输出，并经过 `StateDeltaValidator` 提交。
- NPC 说“我愿意签”不等于状态自动签约。
- 签约必须由 ActionService 或 EventService 结算。

### 6.11 MemoryService

第一阶段：

- 使用 MySQL 存储结构化记忆。
- 每次对话后写入摘要。
- 承诺单独存储。

第二阶段：

- 增加语义检索。
- 增加事件型记忆。
- 增加 NPC 反思摘要。

### 6.12 EndingService

职责：

- 根据 `GameState`、flags、NPC 状态、事件记录判定结局。
- 输出结局 ID、评分和结局文本。
- 保存复盘数据。

结局判定优先级：

1. 强制失败。
2. 隐藏结局。
3. 最佳结局。
4. 标准完成结局。
5. 中间结局。
6. 默认失败结局。

## 7. API 设计

当前 `src/api/server.py` 已用于剧本生成。建议把游戏运行 API 独立到：

```text
src/api/game_routes.py
```

### 7.1 新建游戏

`POST /api/game/session`

请求：

```json
{
  "package_id": "pkg_20260706_001",
  "difficulty": "standard"
}
```

响应：

```json
{
  "session_id": "sess_xxx",
  "game_state": {},
  "visible_npcs": [],
  "opening_text": ""
}
```

该接口要求已登录。服务端从登录态读取 `account_id`，禁止前端自报账号归属。

### 7.2 执行动作

`POST /api/game/session/{session_id}/action`

请求：

```json
{
  "input_mode": "free_text",
  "text": "我想先去找杨德清聊聊补偿方案",
  "action_id": "",
  "target_npc_id": ""
}
```

响应：

```json
{
  "action_result": {},
  "npc_state_evaluation": {},
  "state_delta_validation": {},
  "npc_reply": "",
  "game_state": {},
  "visible_logs": [],
  "need_confirmation": false
}
```

### 7.3 日终

`POST /api/game/session/{session_id}/end-day`

响应：

```json
{
  "day_summary": "",
  "triggered_events": [],
  "night_summary": "",
  "game_state": {},
  "is_ended": false
}
```

### 7.4 读取存档

`GET /api/game/session/{session_id}`

### 7.5 复盘

`GET /api/game/session/{session_id}/review`

### 7.6 继续最近一局

`GET /api/game/session/latest-active`

该接口要求已登录。服务端按当前账号查找最近一局未结束游戏。

查询逻辑：

```sql
select *
from game_sessions
where account_id = ?
  and status in ('active', 'paused')
order by updated_at desc
limit 1;
```

如果不存在未结束游戏，返回 404 或 `{ "session": null }`，由前端展示“新开一局”。

### 7.7 账号登录

`POST /api/auth/login`

请求：

```json
{
  "username": "demo001",
  "password": "plain_password_from_form"
}
```

响应：

```json
{
  "account_id": "acct_xxx",
  "username": "demo001",
  "role": "player"
}
```

服务端同时设置 HttpOnly Cookie。登录失败统一返回 401，不区分用户名不存在或密码错误。

### 7.8 退出登录

`POST /api/auth/logout`

### 7.9 当前账号

`GET /api/auth/me`

响应：

```json
{
  "account_id": "acct_xxx",
  "username": "demo001",
  "role": "player"
}
```

## 8. 前端设计

当前 `frontend/index.html` 继续保留为剧本生成工具。建议新增：

```text
frontend/game.html
```

或在后续切换到：

```text
frontend-app/
  Next.js
  TypeScript
  Tailwind CSS
  Zustand
```

第一阶段如果继续单页 HTML，必须做到：

- 登录页。
- 三栏布局。
- 对话流。
- 行动点显示。
- 日终结算。
- NPC 档案。
- 地图占位。
- 事件日志。
- 存档恢复。

前端状态只作展示。真实状态以服务端 session 为准。

登录页第一阶段只放用户名、密码和登录按钮。不做注册、找回密码、记住我、第三方登录和用户资料页。

### 8.1 前端美术资源加载

前端必须以 `package_id` 为边界加载美术资源：

```text
读取当前 game session
  ↓
取得 session.package_id
  ↓
GET /api/script-packages/{package_id}/assets
  ↓
建立 asset_id 索引、entity 索引和 usage 索引
  ↓
页面组件按 asset_id 或 linked_entity_id 渲染
```

组件不得写死图片路径。示例：

- NPC 对话头像：按 `linked_entity_type=npc`、`linked_entity_id=<npc_id>`、`usage=dialogue`、`state=<emotion>` 查询。
- NPC 档案头像：按 `usage=npc_profile` 查询。
- 地图底图：按 `category=map`、`type=map_base` 查询。
- 地图交互层：读取 `art/map/map_layers.json`，再按 NPC 和事件状态渲染高亮。
- 事件卡片：按 `linked_entity_type=event`、`linked_entity_id=<event_id>`、`usage=event_card` 查询。
- 结局页：按 `linked_entity_type=ending`、`linked_entity_id=<ending_id>`、`usage=ending` 查询。
- 证据库：按 `linked_entity_type=evidence`、`linked_entity_id=<evidence_id>`、`usage=evidence_viewer` 查询。

fallback 规则：

```text
目标状态资源
  ↓ 不存在
同实体 neutral/default 资源
  ↓ 不存在
同类别 default 资源
  ↓ 不存在
system_missing_asset
```

首屏需要预加载：

- 登录页背景和标题资源。
- 当前主界面 UI token。
- 当前玩家最近 session 的地图底图。
- 当前可见 NPC 的头像。

非首屏资源按需加载：

- 事件插图。
- 结局图。
- 复盘图表和证据大图。

前端缺图时必须显示 `system_missing_asset`，同时把缺失 `asset_id` 写入前端错误日志，便于美术和测试修复。

## 9. LLM 调用设计

### 9.1 Prompt 注入顺序

```text
[System] 角色固定档案
[System] 规则边界和禁止事项
[System] 当前 GameState 摘要
[System] 目标 NPC 当前状态
[Memory] 结构化记忆
[History] 最近对话
[Human] 玩家当前行动和话语
```

### 9.2 NPC 指标评估输出格式

NPC 指标评估调用必须输出 JSON，不能输出散文。

```json
{
  "npc_id": "npc_yang_deqing",
  "attitude_delta": {
    "trust_to_player": -6,
    "attitude_score": 3,
    "anxiety_level": 8,
    "reference_point": 0
  },
  "state_flags": {
    "core_demand_satisfied": false,
    "signed_intent": false,
    "will_share_with": ["npc_li_ergu"]
  },
  "reasoning_summary": "玩家没有回应祖坟问题，角色愿意继续谈但信任下降。",
  "dialogue_intent": "继续试探玩家，不直接拒绝",
  "risk_notes": ["祖坟问题未处理"]
}
```

系统必须保存：

- LLM 原始 JSON。
- 校验后的指标变化。
- 被裁剪或拒绝的字段。
- 重试次数。

### 9.3 NPC 对话输出格式

NPC 对话可以是自然语言，但服务端内部需要附带结构化审计结果：

```json
{
  "reply": "你先别跟我讲大道理，我就问一句，我家那块地怎么算？",
  "risk_flags": [],
  "claimed_facts": ["询问土地补偿"],
  "requires_followup_evaluation": false
}
```

对话输出不承担指标结算。指标变化由 NPC 指标评估输出承担。

### 9.4 输出校验

校验器检查：

- 是否泄露隐藏信息。
- 是否越过知识边界。
- 是否产生非法承诺。
- 是否改变剧情事实。
- 是否与 NPC 风格严重不符。
- 指标变化是否超过允许范围。
- 是否试图直接设置 `signed: true`。

失败处理：

1. 用更严格 prompt 重试一次。
2. 再失败则返回模板化回应。
3. 记录 LLM 输出失败日志。

## 10. 外部项目复用建议

以下项目可作为稳定代码或架构参考。复用前必须检查许可证，并在本仓库保留来源说明。

### 10.1 Generative Agents

项目：

- `https://github.com/joonspk-research/generative_agents`

可复用部分：

- `reverie/backend_server/persona/memory_structures/associative_memory.py`
- `reverie/backend_server/persona/memory_structures/scratch.py`
- `reverie/backend_server/persona/memory_structures/spatial_memory.py`
- `reverie/backend_server/persona/cognitive_modules/`

适合复用到：

- NPC 记忆流。
- 记忆重要性、最近性、相关性检索。
- NPC 反思摘要。
- NPC 日程和行为计划。

复用方式：

- 不建议直接照搬完整项目。该项目是研究原型，依赖旧式目录和环境。
- 建议把记忆结构和检索打分思想改写为本项目的 `src/services/memory_service.py`。
- `associative_memory.py` 的记忆节点思想可改造成 `NPCMemoryEvent`。
- `cognitive_modules` 的 perceive/retrieve/reflect/plan 可改造成夜间推演中的可选步骤。

### 10.2 AI Town

项目：

- `https://github.com/a16z-infra/ai-town`

可复用部分：

- `ARCHITECTURE.md` 的分层方案。
- `convex/engine/` 的游戏引擎与业务规则分离思路。
- `convex/agent/` 的 Agent 异步处理思路。
- `convex/aiTown/agent.ts` 的游戏循环中 Agent 提交输入的思路。

适合复用到：

- 游戏循环架构。
- 服务端状态为准。
- 人类和 Agent 都通过输入提交到引擎。
- 前端只渲染状态。

复用方式：

- 本项目后端是 Python FastAPI，不建议直接迁移 Convex。
- 可以借鉴 `convex/engine` 的职责边界，在本项目中实现 `GameEngineService`。
- 可以借鉴 Agent 不直接改状态、只提交输入的模式。

### 10.3 LangGraph

项目：

- `https://github.com/langchain-ai/langgraph`

可复用部分：

- 状态图。
- durable execution。
- human-in-the-loop。
- short-term 和 long-term memory 接口。

适合复用到：

- 玩家输入解析流程。
- NPC 对话流程。
- 夜间高焦虑 NPC 行为流程。
- 失败重试和人工审查。

复用方式：

- 第一阶段不引入，避免增加复杂度。
- 第二阶段如果 NPC 流程复杂，可新增 `src/agents/`，用 LangGraph 编排：
  `parse_action -> compliance_check -> rule_settlement -> npc_reply -> output_validation`。

### 10.4 LangGraph Memory Service

项目：

- `https://github.com/langchain-ai/langgraph-memory`

可复用部分：

- `memory_service/graph.py`
- `tests/evals/test_memories.py`

适合复用到：

- 长期记忆抽取。
- 事件型记忆。
- 记忆 schema 持续更新。
- 记忆评估测试。

复用方式：

- 第一阶段只做结构化 JSON 记忆。
- 第二阶段可参考 `memory_service/graph.py`，把对话日志抽取成 NPC 记忆。
- 测试思路可改造成本项目的 NPC 记忆验收测试。

### 10.5 LangGraph Memory Agent

项目：

- `https://github.com/langchain-ai/memory-agent`

可复用部分：

- ReAct Agent 调用 `store_memory` 工具的模式。
- 按用户或线程隔离记忆的模式。

适合复用到：

- 玩家长期偏好记录。
- NPC 对玩家历史行为的记忆。
- 研究后台中的访谈式分析助手。

复用方式：

- 不建议让 NPC 自主决定保存所有记忆。
- 可以把 `store_memory` 模式改成受规则控制的 `record_npc_memory`。

### 10.6 AWS Dynamic Game NPC Dialogue

项目：

- `https://github.com/aws-solutions-library-samples/guidance-for-dynamic-game-npc-dialogue-on-aws`

可复用部分：

- NPC 知识库 RAG。
- NPC LLMOps。
- 质量审查流程。

适合复用到：

- 后期生产部署。
- NPC 知识库更新。
- 质量评估和回归测试。

复用方式：

- 该项目偏 AWS、Unreal、MetaHuman，当前阶段不直接复用代码。
- 可复用 RAG 和 QA 流程设计。

### 10.7 不建议直接复用的部分

- 完整 AI Town 前端地图和实时移动系统。当前游戏是治理对话和决策，地图是辅助信息，不需要实时角色移动。
- 完整 Generative Agents 小镇环境。它的空间模拟和日程机制超出第一阶段需求。
- Unreal/MetaHuman 相关代码。当前目标是桌面浏览器内测。

## 11. 状态流转

### 11.1 白天行动

```text
玩家输入
  ↓
选择目标 NPC / 场景
  ↓
玩家输入说服文本
  ↓
ActionParserService 辅助归类
  ↓
ComplianceService
  ↓
ActionService
  ↓
NPCStateEvaluationService 将玩家原话传给目标 NPC LLM
  ↓
StateDeltaValidator
  ↓
DialogueService
  ↓
SessionRepository.save()
```

每次 `ActionService` 和 `DialogueService` 产生稳定结果后，必须调用 `SessionRepository.save_current_snapshot()`。保存失败时，接口应返回错误，不能让前端继续显示一个未持久化的状态。

### 11.2 日终夜间

```text
End Day
  ↓
EventService.check_events()
  ↓
NightSimulationService.run_night()
  ↓
GameState.day + 1
  ↓
SessionRepository.save()
```

日终和夜间推演后必须保存当前状态，并写出 JSON 快照。

### 11.3 结局

```text
D90 或强制失败条件
  ↓
EndingService.evaluate()
  ↓
ReviewService.build_review()
  ↓
SessionRepository.save(status="ended")
```

结局判定后必须保存最终状态、结局结果和复盘数据。

## 12. 存储设计

### 12.1 第一阶段 MySQL 存储

第一阶段采用 MySQL。原因是该项目按正式系统设计，账号、存档、行为日志、研究数据和后续多人访问都需要稳定的关系型数据库承载。开发环境可以用本地 MySQL 或 Docker MySQL，生产环境沿用 MySQL，避免后续从 SQLite 迁移到正式数据库时重做数据访问层。

JSON 文件仍可作为快照、调试和复盘辅助产物保留，但不作为主存储。

建议新增：

```text
data/
  mysql/
outputs/game_sessions/
  sess_xxx/
    snapshots/
    logs.jsonl
    dialogues.jsonl
    review.json
```

核心表：

```sql
create table accounts (
  account_id varchar(64) primary key,
  username varchar(128) not null unique,
  password_hash varchar(255) not null,
  password_hash_scheme varchar(32) not null,
  role varchar(32) not null,
  disabled tinyint(1) not null default 0,
  created_at datetime(6) not null,
  metadata_json json not null
) engine=InnoDB default charset=utf8mb4 collate=utf8mb4_unicode_ci;

create table auth_sessions (
  token_hash varchar(128) primary key,
  account_id varchar(64) not null,
  created_at datetime(6) not null,
  expires_at datetime(6) null,
  revoked_at datetime(6) null,
  constraint fk_auth_sessions_account
    foreign key(account_id) references accounts(account_id)
) engine=InnoDB default charset=utf8mb4 collate=utf8mb4_unicode_ci;

create table game_sessions (
  session_id varchar(64) primary key,
  account_id varchar(64) not null,
  package_id varchar(128) not null,
  status varchar(32) not null,
  created_at datetime(6) not null,
  updated_at datetime(6) not null,
  current_snapshot_json json not null,
  metadata_json json not null,
  constraint fk_game_sessions_account
    foreign key(account_id) references accounts(account_id),
  index idx_game_sessions_account_updated(account_id, updated_at),
  index idx_game_sessions_package(package_id)
) engine=InnoDB default charset=utf8mb4 collate=utf8mb4_unicode_ci;

create table game_snapshots (
  snapshot_id varchar(64) primary key,
  session_id varchar(64) not null,
  account_id varchar(64) not null,
  reason varchar(64) not null,
  day int not null,
  action_index int not null,
  snapshot_json json not null,
  json_file_path varchar(512) null,
  created_at datetime(6) not null,
  constraint fk_game_snapshots_session
    foreign key(session_id) references game_sessions(session_id),
  constraint fk_game_snapshots_account
    foreign key(account_id) references accounts(account_id),
  index idx_game_snapshots_session_created(session_id, created_at),
  index idx_game_snapshots_account_created(account_id, created_at)
) engine=InnoDB default charset=utf8mb4 collate=utf8mb4_unicode_ci;
```

Session token 只把原文返回到 HttpOnly Cookie。数据库中只保存 token hash。

`game_sessions.current_snapshot_json` 是恢复游戏的权威当前状态。`game_snapshots` 保存历史快照索引，便于复盘和研究查询。

### 12.2 JSON 快照

建议新增：

```text
outputs/game_sessions/
  sess_xxx/
    session_manifest.json
    snapshots/
      0001_opening.json
      0002_day001_after_action_home_visit.json
      0003_day001_after_npc_reply.json
      0004_day001_after_night.json
    logs.jsonl
    dialogues.jsonl
    review.json
```

用途：

- 便于调试。
- 便于人工检查。
- 便于复盘导出。
- 便于在 LLM 或规则服务故障后排查问题。

限制：

- JSON 快照不是主存储。
- 恢复游戏状态以 MySQL 中的当前 session 记录为准。
- JSON 快照可用于审计和回放，但不能替代数据库事务。
- 第一阶段不支持玩家从任意 JSON 快照回滚继续游戏。

### 12.3 后续存储扩展

建议：

- Redis：会话缓存和短期状态。
- 向量库：NPC 记忆和知识库检索，可选。

后续需要补全：

- 数据库迁移工具。
- MySQL 备份与恢复策略。
- 服务器连接池配置。
- 管理员创建账号页面。
- 用户角色权限表。
- 密码重置表。
- 登录失败审计。
- 数据匿名化导出表。
- 实验分组表。

## 13. 测试插桩

所有服务输出必须可测试：

- 不依赖真实 LLM。
- 可注入 fake LLM。
- 可注入固定随机种子。
- 可读取 fixture 剧本包。
- 可回放日志重建状态。

## 14. 迁移策略

第一步只新增模块，不破坏剧本生成器。

建议新增文件：

```text
src/domain/account.py
src/services/account_service.py
src/services/auth_session_service.py
src/services/action_service.py
src/services/event_service.py
src/services/night_simulation_service.py
src/services/game_session_service.py
src/services/ending_service.py
src/persistence/account_repository.py
src/persistence/game_session_repository.py
src/api/auth_routes.py
src/api/game_routes.py
scripts/create_account.py
tests/test_account_service.py
tests/test_auth_api.py
tests/test_action_service.py
tests/test_event_service.py
tests/test_night_simulation_service.py
tests/test_game_session_service.py
```

第二步在 `src/api/server.py` 中挂载 game routes。

第三步新增 `frontend/game.html`。

第四步再考虑 React/Next.js 重构。

## 15. 针对“钞越任务”的交付补充

本节用于直接回应当前任务：“结合剧本 prompt、v01 版本剧情、市面上类似项目，梳理开发技术路线、对美工的要求、开发过程中需要进一步讨论的细节。”

### 15.1 本轮资料核验结论

当前 `background/` 目录中可读取的资料包括：

- `剧本prompt.txt`
- `游戏设计注意点.txt`
- `product_requirements.md`
- `technical_design.md`
- `test_plan.md`

当前工作区没有找到 `outputs/script_drafts/v01/`。因此本文档已先按 `剧本prompt.txt` 与现有产品/测试文档补齐整体设计。后续一旦恢复 `outputs/script_drafts/v01/`，必须补做一次 v01 剧情接入检查。

v01 剧情接入检查项：

- 核对 v01 的游戏标题、章节数量、NPC 名单、结局数量。
- 抽取 v01 中的固定事件、决策节点、暗线线索、结局条件。
- 将 v01 的 NPC 名称映射为稳定 `npc_id`，禁止用中文名作为主键。
- 将 v01 的章节和事件映射为稳定 `chapter_id`、`event_id`。
- 核对 v01 中的指标名称和本文档中的运行时指标是否一致。
- 将 v01 中的美术和 UI 制作备注转成 `assets_manifest.json`。
- 运行剧本包兼容性测试，确认 v01 可被游戏运行时加载。

### 15.2 类似项目启发

市面和开源项目对本项目的启发如下：

| 项目 | 可借鉴点 | 本项目取舍 |
|---|---|---|
| Generative Agents | 记忆、反思、计划、可信行为模拟 | 借鉴 NPC 记忆和反思机制，不照搬小镇空间模拟 |
| AI Town | 服务端状态、AI 角色社交、可部署样板 | 借鉴游戏循环和状态权威源，不照搬实时移动地图 |
| Interactive LLM Powered NPCs | LLM NPC 对话、语音/文本交互 | 借鉴 NPC 对话体验，第一版不做语音 |
| LLM 驱动 NPC 商业游戏案例 | 动态对话带来沉浸感，也容易被玩家诱导跑偏 | 必须设置知识边界、结构化指标输出和违规校验 |
| AgentSociety / 社会仿真类项目 | 多主体社会模拟、行为日志、实验回放 | 借鉴研究数据和社会网络分析，不做大规模仿真平台 |

这些项目说明：LLM NPC 的价值在于“角色能基于记忆和处境做出自己的判断”。本项目不能只做固定数值表。核心技术路线应保持“LLM 角色自评 + 系统校验提交”的结构。

### 15.3 开发技术路线和整体思路

第一阶段目标不是重写剧本生成器，而是做出能跑完整一局的游戏运行时。

整体路线：

```text
剧本 prompt / v01 剧情
  ↓
剧本包标准化
  ↓
账号登录与 MySQL 存储
  ↓
GameSession 和自动存档
  ↓
玩家行动解析与合规检查
  ↓
NPC LLM 指标评估
  ↓
NPC 对话生成
  ↓
事件系统和夜间推演
  ↓
结局判定
  ↓
复盘与数据导出
```

#### 15.3.1 剧本包先行

剧本会持续修改。所有开发必须围绕剧本包工作。

剧本包至少包括：

- `package_manifest.json`
- `full_script.md`
- `script_structure.json`
- `npc_profiles.json`
- `action_rules.json`
- `event_rules.json`
- `ending_rules.json`
- `prompt_templates/`
- `assets_manifest.json`

每局开局锁定一个 `package_id`。旧局不随新剧本变化。

#### 15.3.2 MySQL 作为权威数据源

第一版就使用 MySQL。账号、存档、当前状态、历史快照索引、日志索引、研究数据都进入 MySQL。JSON 快照只用于调试、复盘和证据保留。

必须优先实现：

- `accounts`
- `auth_sessions`
- `game_sessions`
- `game_snapshots`
- `action_logs`
- `dialogue_logs`
- `npc_state_evaluations`
- `event_logs`
- `night_logs`

#### 15.3.3 账号和存档作为第一版基础能力

第一版必须有登录、自动存档、继续最近一局。

最小闭环：

```text
登录
  ↓
继续最近一局 / 新开一局
  ↓
每次行动后保存 current_snapshot_json
  ↓
写 MySQL 快照索引
  ↓
写 JSON 快照文件
  ↓
读档时以 MySQL current_snapshot_json 恢复
```

#### 15.3.4 NPC 指标变化由 LLM 角色自评

玩家每次与 NPC 互动后，系统将以下内容传给目标 NPC 的 LLM：

- 玩家输入的完整说服文本。
- NPC 固定档案。
- NPC 当前指标。
- NPC 记忆。
- 玩家行动解析结果。
- 当前世界状态。
- 相关事件和承诺。
- NPC 的红线、收益函数、知识边界。

LLM 输出固定 JSON。系统只负责校验、裁剪、重试和提交。

本项目的核心体验是“玩家用文字说服 NPC”。按钮、快捷行动和行动解析只是辅助输入和合规检查。NPC 指标变化不能由固定规则表直接决定，必须由目标 NPC 的大模型依据自身人设、规则、记忆和上下文判断本次说服带来的态度变化。

#### 15.3.5 夜间推演采用规则和 LLM 混合

普通传播用规则处理。关键 NPC、高焦虑 NPC、暗线相关 NPC 可调用 LLM 自评。

夜间推演输出三类结果：

- 玩家不可见的完整夜间日志。
- 玩家次日可见的失真摘要。
- NPC 状态变化和事件候选。

#### 15.3.6 前端第一版使用游戏页，不复用剧本生成页

现有页面用于剧本生成和审稿。游戏需要新增页面。

第一版页面：

- 登录页。
- 游戏首页：继续游戏 / 新开一局。
- 游戏主界面：三栏布局。
- 日终结算页。
- 复盘页。

### 15.4 对美工的要求

美术需求已独立成 `art_requirement.md`，该文件是美术制作、UI 设计、前端接入和测试验收的正式依据。本文档只保留技术侧必须遵守的接入原则。

美术资源必须服务于“基层治理推演”的真实感和信息清晰度。第一版不追求高成本动画，优先保证统一风格、状态可读、剧情沉浸和程序可接入。

技术侧必须支持的美术交付范围：

- 视觉风格板和 `design_tokens.json`。
- 登录页、游戏首页、三栏主界面、日终结算页、复盘页、结局页设计。
- UI 组件和图标系统。
- NPC 头像和后续情绪差分。
- 手绘地图、SVG 交互图层和 `map_layers.json`。
- 场景背景图。
- 事件插图。
- 证据和档案图。
- 结局图。
- 加载、空状态、错误状态图。

所有资源必须进入 `assets_manifest.json`，并满足以下条件：

- 有稳定 `asset_id`。
- 有 `category`、`type`、`usage`。
- 能关联到 `npc_id`、`event_id`、`location_id`、`evidence_id` 或 `ending_id`。
- 有尺寸、格式、版本、授权、fallback 说明。
- 路径真实存在，且位于当前剧本包 `art/` 目录下。

技术侧验收标准：

- 后端能加载并校验 `assets_manifest.json`。
- 前端能按 `asset_id`、`linked_entity_id`、`usage` 和 `state` 查询资源。
- 缺失目标资源时能按 fallback 链显示默认资源。
- 旧存档继续使用开局时锁定剧本包中的旧资源。
- 地图元素能按 NPC 状态、事件状态和风险等级变色或换图标。
- 资源缺失、尺寸错误、授权字段缺失必须在剧本包兼容性测试中报错。

### 15.5 开发过程中需要进一步讨论的细节

以下问题必须在正式开发前或 M1 阶段逐项确认。

#### 15.5.1 剧本规模

- 第一版到底使用 15 个 NPC 还是 36 个 NPC。
- 90 行动日是否完整实现，还是先实现压缩版内测。
- v01 剧情是否作为第一版唯一剧情包。
- 结局数量以 v01 为准，还是先固定 8 个主结局。

#### 15.5.2 LLM 调用策略

- NPC 指标评估用哪个模型。
- NPC 对话用哪个模型。
- 夜间推演哪些 NPC 调用 LLM。
- 每局最大 LLM 调用次数。
- 每局成本上限。
- 是否需要本地模型备选。

#### 15.5.3 NPC 指标 Schema

需要最终确认每个 NPC 的运行时指标：

- `trust_to_player`
- `attitude_score`
- `anxiety_level`
- `reference_point`
- `core_demand_satisfied`
- `signed`
- `petition_risk`
- `mobilization_tendency`
- `media_contact_willingness`
- `hidden_clue_awareness`

第一版可以先实现前 6 个，其余指标保留在 `metadata`。

#### 15.5.4 签约判定

需要明确：

- LLM 输出 `signed_intent: true` 后，系统还需要哪些条件才改成 `signed: true`。
- 是否必须消耗行动点完成正式签约。
- 是否必须有预算余额。
- 是否必须满足核心诉求。
- 是否允许后续反悔。

#### 15.5.5 承诺机制

需要明确：

- 玩家承诺如何被识别。
- 承诺是否需要玩家二次确认。
- 承诺如何绑定预算和行动期限。
- 未兑现承诺如何惩罚。
- NPC 如何在后续对话中提起承诺。

#### 15.5.6 夜间信息传播

需要明确：

- 社会关系矩阵由剧本生成，还是人工维护。
- 信息传播是否有概率。
- 金额传闻是否默认夸大。
- 哪些信息永远不可传播。
- 暗线证据是否可能被 NPC 转移。

#### 15.5.7 研究数据

需要明确：

- 是否采集玩家输入原文。
- 是否采集 LLM 完整输出。
- 是否需要对外导出匿名化数据。
- 是否区分正式局、测试局、沙盒局。
- 是否需要实验分组字段。

#### 15.5.8 后台管理

需要明确：

- 谁创建账号。
- 谁发布剧本包。
- 谁能查看全部存档。
- 谁能导出研究数据。
- 是否需要禁用某个剧本包。
- 是否需要重跑剧本包兼容性测试。

#### 15.5.9 美术生产边界

需要明确：

- 第一版头像数量。
- 是否需要情绪头像。
- 地图是 SVG 还是位图。
- 事件图数量。
- 是否允许 AI 生成图作为初稿。
- 最终商用授权如何处理。

### 15.6 最后一轮流程检查

以下流程必须在开发前确认全部闭环。

#### 15.6.1 玩家主流程

```text
登录
  ↓
继续最近一局 / 新开一局
  ↓
读取剧本包
  ↓
进入 D1
  ↓
玩家行动
  ↓
NPC 指标评估
  ↓
NPC 回复
  ↓
自动存档
  ↓
日终结算
  ↓
夜间推演
  ↓
自动存档
  ↓
进入下一日
  ↓
D90 或强制结局
  ↓
复盘
```

检查结果：当前文档已覆盖主流程。

#### 15.6.2 NPC 指标流程

```text
玩家原话
  ↓
选择目标 NPC
  ↓
行动解析辅助归类
  ↓
合规检查
  ↓
目标 NPC LLM 阅读玩家说服文本并自评
  ↓
结构化 JSON
  ↓
系统校验
  ↓
状态提交
  ↓
日志记录
```

检查结果：当前文档已覆盖。后续要补正式 JSON Schema 文件。

#### 15.6.3 存档流程

```text
稳定状态产生
  ↓
写 MySQL current_snapshot_json
  ↓
写 game_snapshots 索引
  ↓
写 JSON 快照
  ↓
返回前端
```

检查结果：当前文档已覆盖。后续要补数据库迁移脚本和失败回滚策略。

#### 15.6.4 剧本更新流程

```text
修改剧本
  ↓
重新抽取结构化 JSON
  ↓
生成新 script_package
  ↓
运行兼容性测试
  ↓
发布新 package_id
  ↓
新局使用新包，旧局继续旧包
```

检查结果：当前文档已覆盖原则。后续要补后台发布页面和版本治理细则。

#### 15.6.5 美术接入流程

```text
根据 art_requirement.md 制作资源
  ↓
导出到 script_package/art/
  ↓
写入 assets_manifest.json、design_tokens.json、map_layers.json
  ↓
AssetManifestService 校验
  ↓
前端按 package_id 和 asset_id 加载
  ↓
按 NPC/事件状态切换
  ↓
复盘和结局复用
```

检查结果：本文档已与 `art_requirement.md` 同步。后续要根据 v01 剧情列具体 NPC、事件、证据、地点和结局资产清单。

#### 15.6.6 测试流程

```text
单元测试
  ↓
服务集成测试
  ↓
API 测试
  ↓
前端 E2E
  ↓
人工试玩
  ↓
剧本包回归测试
```

检查结果：`test_plan.md` 已覆盖。后续要补 fixture 和真实测试账号。

### 15.7 当前仍缺的输入

开发前仍需补齐：

- `outputs/script_drafts/v01/` 完整剧情产物。
- 第一版 NPC 最终名单。
- 第一版结局最终名单。
- 第一版地图草图。
- 第一版美术风格参考图。
- 第一版美术资产清单和 `assets_manifest.json` 初版。
- 第一版 MySQL 连接配置。
- 第一版 LLM 模型和 API 配置。
- 是否需要后台管理页的最小版本。

## 16. 运行时闭环与实现补充

### 16.1 剧本运行时契约

`full_script.md` 是供人阅读和编辑的母稿；游戏运行时不得直接依赖其中的自然语言段落推断规则。每个可发布剧本包还必须提供以下结构化内容：

```text
story_beats.json                 # 关键日、事件节拍、提前结局、可快进日
interaction_opportunities.json   # 玩家获得 NPC 对话/行动机会的契约
facts_and_clues.json             # 已知事实、隐藏事实、证据和知识归属
npc_profiles.json                # 角色档案、边界、收益、记忆种子、语言风格
action_rules.json                # 资源与程序规则
event_rules.json                 # 事件触发与后果
ending_rules.json                # 结局必要条件和评分
```

`interaction_opportunities.json` 的最小字段如下：

```json
{
  "opportunity_id": "opp_d07_yang_home_visit",
  "npc_id": "npc_yang_deqing",
  "entry_type": "map_visit",
  "available_when": {
    "day_min": 7,
    "requires_events": ["event_d07_elder_visit"],
    "requires_clues": [],
    "relationship": {"trust_to_player_gte": 0}
  },
  "cost": {"action_points": 1, "budget": 0},
  "conversation_scope": ["compensation", "ancestral_graves"],
  "forbidden_topics": ["hidden_corruption_evidence"],
  "close_when": ["npc_signed", "event_d20_collective_negotiation"],
  "on_complete": {"may_unlock": ["opp_archive_ancestral_record"]}
}
```

`ScriptPackageValidator` 必须在发布前校验：所有 `npc_id`、`event_id`、地点、线索、结局、资源主键可解析；不存在悬空前置条件；所有关键日有可玩入口；所有结局都至少能从一个合法状态到达；母稿与结构化包的版本号一致。

### 16.2 机会服务与玩家交互入口

新增：

```text
src/services/interaction_opportunity_service.py
src/domain/interaction_opportunity.py
tests/test_interaction_opportunity_service.py
```

`InteractionOpportunityService.list_available(session)` 根据当前日期、地点、事件、线索、NPC 状态、关系和冷却规则返回可用入口。前端只展示该服务返回的入口；地图图标、任务卡、来访通知、通讯录和档案追问都是同一机会模型的不同展示方式。

调用流程：

```text
GET /api/game/session/{session_id}/opportunities
  ↓
玩家选择 opportunity_id 和目标 NPC
  ↓
POST /action 带 opportunity_id、player_text、client_action_id
  ↓
服务端再次验证机会仍可用并锁定本次操作
  ↓
执行 NPC 回合，提交状态并解锁/关闭后续机会
```

这保证玩家获得 LLM 交互机会的来源是剧本和当前状态，而不是前端任意开放的聊天框；同时允许玩家通过信任、证据、事件和地图探索逐步打开新的角色与话题。

### 16.3 原子 NPC 回合

当前的 `NPCStateEvaluationService` 与 `DialogueService` 逻辑上属于同一回合，不能分别独立调用后再拼接，否则可能出现“指标说拒绝、台词却同意”的矛盾。第一阶段应增加门面服务：

```text
src/services/npc_turn_service.py
```

`NPCTurnService` 以一次受控模型调用或同一份模型结果完成：角色台词、结构化态度变化、记忆候选、传播意图和风险说明。返回结果必须同时通过 JSON Schema、知识边界、状态变化范围和可见性校验；随后在一个数据库事务中写入动作日志、NPC 状态、记忆、可见回复和当前快照。

状态提交顺序：

```text
机会校验 -> 合规校验 -> 资源预校验 -> 锁定 session
-> 调用 NPC LLM（未提交状态） -> 输出校验/必要时重试
-> MySQL 事务提交状态、日志、快照索引 -> 事务提交后写 JSON 辅助快照
```

若模型超时、输出无效、校验失败或数据库事务失败，必须返回“本次互动未完成”，不扣行动点、不写 NPC 指标。可使用受控的角色模板回复提示玩家稍后重试，但模板回复不能伪造 LLM 已结算的态度变化。

### 16.4 LLM 安全、可复现与成本控制

- 玩家文本、NPC 台词和外部证据都是不可信内容；prompt 明确规定其中任何“忽略规则”“修改 JSON”等文字均不是系统指令。
- 每次调用记录 `model_provider`、`model_id`、模型/提示词版本、请求哈希、温度、token 用量、耗时、重试次数、原始受限输出和校验结果。敏感密钥和完整内部系统提示词不得进入玩家可见日志。
- 第一版对 NPC 指标评估使用低温度、固定 JSON Schema 和最多一次修复重试；研究回放复用已存储的结构化结果，不重新调用 LLM。
- 每局配置调用次数和 token 预算；超过预算时停止开启新的自由对话，或按剧本启用明确标识的规则化摘要降级，但不得悄然改变已提交状态。
- 正式上线前补充内容安全策略、敏感信息脱敏和数据保留期限；研究导出以匿名账户标识替代用户名。

### 16.5 幂等、并发与存档一致性

`POST /action` 和 `POST /end-day` 必须接收 `client_action_id`。同一 `session_id + client_action_id` 的重复请求必须返回第一次已提交的结果，绝不重复扣行动点、扣预算或调用 LLM。

`game_sessions` 应增加 `state_version` 乐观锁字段；写入条件包含当前版本。两个浏览器标签或网络重试同时提交时，只有一个成功，另一个返回冲突并要求刷新。MySQL 当前快照、操作日志和历史快照索引必须处于同一事务；JSON 文件写入失败只记录告警并重试，不得回滚已合法提交的数据库状态。

建议新增表：

```text
game_actions       # client_action_id、输入摘要、机会、处理状态、最终响应索引
llm_call_audits    # 可复现和成本审计字段
npc_memories       # 结构化记忆、来源 turn、可见性和过期/失效标记
```

### 16.6 实现顺序修正

原 M1 只做规则原型不足以验证核心玩法。建议顺序调整为：

1. M0：确定上述剧本 schema、版本治理、状态事务和 fake LLM 契约。
2. M1：实现 `InteractionOpportunityService`、单 NPC 原子回合、存档和一条从 D1 到小结局的垂直切片。
3. M2：扩展白天、事件、夜间、地图入口、复盘和完整剧本包校验。
4. M3：接入真实 LLM、成本控制、记忆压缩与对抗难度。
5. M4：研究后台、实验分组、匿名导出与权限体系。

这样可最早验证“玩家文字 -> NPC LLM 态度判断 -> 系统提交 -> 新机会”的游戏核心，而不是先做大量固定行动后再回头替换结算逻辑。

### 16.7 当前工作区基线与开工门槛

本次核查中，`serious_game_code` 工作区当前只有背景资料、设计文档和美术参考图，未发现可执行的应用源码、依赖清单、数据库迁移文件、`outputs/script_drafts/v01/` 成品或已初始化 Git 仓库。因此本文档中的“已有 `src/`、`frontend/` 模块”只能作为原始项目预期结构，不能视为已在当前目录验证过的事实。

开始编码前必须完成以下基线动作：

1. 将正式源码仓库完整放入 `serious_game_code`，并保留 Git 历史与依赖文件。
2. 导入或生成第一版可校验剧本包，不以 `剧本prompt.txt` 代替运行时 JSON。
3. 冻结 M1 垂直切片的 NPC、机会、事件、结局和美术主键清单。
4. 建立本地 MySQL、环境变量模板、数据库迁移工具和 fake LLM 测试客户端。
5. 运行 `ScriptPackageValidator`、数据库迁移、单元测试和从 D1 到小结局的端到端验收。

在这五项完成前，设计已足以指导实现，但尚不能宣称项目已经具备可运行的工程基线。
