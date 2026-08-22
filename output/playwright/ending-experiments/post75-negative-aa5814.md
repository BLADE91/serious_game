# D76+ 公开签约奖励负测（aa5814）

- 环境：HEAD `aa58140870367c2969ea53f0941e0d728e0c2714`；test/memory/Fake/default `pkg_gameplay_v3`；headed Chromium。
- 会话：`sess_aca2e08f3ac80e039de6de27fe9659c5`；玩家正常路径推进到 D77；负测前权威签约 30/36。
- 合同：`contract_3037a3c870cd7628ccf1`，LAO-01 老倔头，状态 `awaiting_terms`。

## UI 观察

逐户合同对话框的事实与程序确认区显示禁用且未勾选的控件“公开签约奖励已于D75截止”；玩家无法在 D77 选择该奖励。1920×1080 与 1366×768 下对话框和主要控件可达；console 0 error / 0 warning。

## 公开 API 负测

请求：D77 合法政策、30 万现金、房源、助老资源及程序确认保持完整，仅伪造 `public_window_reward=true`。

响应：HTTP 409；`ACTION_UNAVAILABLE`；“D75后不再适用公开签约奖励，请取消该奖励”。

原子性对比：请求前后 `state_version=306`；合同 DTO 完全相同；资源 DTO 完全相同；合同仍为 `awaiting_terms`、`term_sheet=null`、未预占。

随后按公开规则改为 `public_window_reward=false` 并使用可用的 D60 80㎡房源：条款 200（draft）→审阅 200（accepted）→本人签署 200（signed）；权威签约升至 31/36。

## 证据

- `post75-probe-aa5814.json`：自然推进至 D77 的公开玩家路径与合同批次。
- `post75-negative-aa5814/d77-reward-disabled.png`：1920×1080 UI 禁用状态。
- `post75-negative-aa5814/d77-reward-disabled-1366.png`：1366×768 UI 禁用状态。

## 结论

修复通过：D76+ UI 不可选择早签奖励；公开 API 伪造 true 在条款入口即 409，且无状态/资源副作用；合法 false 路径可继续签署，无 500。
