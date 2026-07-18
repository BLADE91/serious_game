# M3 真实角色 LLM 运行时

更新时间：2026-07-18

## 运行边界

真实模型只负责生成 NPC 可见台词及受限软状态候选。签约户数、预算、全局指标、旗标、事实释放、强制决策、事件与结局轴仍由权威规则层决定。模型调用发生在“短预留”和“短提交”之间，不持有数据库锁。

## Qwen 配置

复制 `.env.example` 为 `.env`，由本机填写 API Key。代码与日志不得保存或输出密钥。

```dotenv
ROLE_LLM_PROVIDER=openai_compatible
ROLE_LLM_BASE_URL=https://api.qianzhang-ai.cn/v1
ROLE_LLM_MODEL=qwen3.6-plus
ROLE_LLM_API_KEY_ENV=DASHSCOPE_API_KEY
DASHSCOPE_API_KEY=
```

供应商使用 JSON mode，本地使用 Pydantic 严格 Schema 校验。鉴权错误 401/403 是不可重试配置错误，不允许静默降级；超时、429 和 5xx 可按上限重试，耗尽后才允许 Fake LLM 降级。

## M3.1–M3.6 实现

| 子项 | 实现 |
|---|---|
| M3.1 | OpenAI Chat Completions 兼容网关；环境变量配置；模型、端点、超时和降级策略集中管理。 |
| M3.2 | `role-turn-v1` 版本提示词；角色设定、可见世界、允许事实、禁泄漏事实标记和记忆分层注入；JSON 严格校验和业务二次校验。 |
| M3.3 | SQLite/内存调用审计；请求哈希幂等复用；瞬时错误有界重试；单日/单局次数与 Token 预算。审计不保存 API Key 或原始玩家文本。 |
| M3.4 | NPC episode/summary 记忆；关键词与近因检索；达到阈值后确定性压缩；TTL 自动过期；显式 invalidation；指令型记忆拒绝写入。 |
| M3.5 | 覆盖非法 JSON、提示词攻击、内部字段输出、越权事实、超时、鉴权失败、预算耗尽和 Fake 降级。 |
| M3.6 | 使用固定随机种子，让 Fake 输出与相反方向的合法 LLM 输出分别完成 D1–D90；两者 75 个硬决策、台账和终局完全一致。 |

## 真实连通性验收

第三方 OpenAI 兼容端点 `https://api.qianzhang-ai.cn/v1/chat/completions` 已完成关闭 Fake 降级的实测。`qwen3.6-plus` 回答通过结构、枚举、知识边界和软状态一致性校验，随后完成 D1–D90 回放：75 个硬决策、89 次夜间结转，终局为 `ending_06 / ending_06d`。审计仅有 1 条 `openai_compatible/succeeded` 记录，输入 1827 tokens、输出 393 tokens、无重试、无降级。

该服务为第三方兼容层。正式生产部署前仍应单独确认余额告警、服务商并发/RPM/TPM、数据保存政策、流式输出和长上下文稳定性；这些属于部署与供应商治理，不改变 M3 已完成的游戏运行时边界。
