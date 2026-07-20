# 最小文字终端客户端

本客户端是前端美术与交互方案冻结前的测试入口。它只调用 `code/backend` 的玩家 API，不读取后端源码、内部状态或剧本配置。

## 启动

先在一个终端启动后端：

```powershell
Set-Location E:\严肃游戏\serious_game_code\code\backend
python -m pip install -r requirements-dev.txt
$env:PYTHONPATH = "src"
python run_server.py
```

再开一个终端运行文字客户端：

```powershell
Set-Location E:\严肃游戏\serious_game_code\code\frontend\terminal
python main.py
```

默认启动后进入编号菜单，普通玩家只需输入菜单前的数字：

```text
【账号入口】
  1. 登录已有账号
  2. 注册新账号
  0. 退出程序
请选择序号：2

【游戏入口】
  1. 开始新游戏并选择出身
  2. 退出当前账号
  0. 退出程序
请选择序号：1
```

之后客户端会依次显示出身、决策、NPC、行动、日终、地图、知识和复盘菜单。交谈对象菜单会先显示人物姓名、公开身份、简短介绍、前情提要、会谈方向和行动点消耗。确认进入时只扣一次行动点，随后可以不限轮次地继续输入自由对话；每轮后都可用编号菜单主动结束，NPC 也可能根据人物底线和上下文送客或离场。未结束的会谈会随存档保存，重启后可继续。普通选择只输入序号；排序题逐项选择优先级；分配题逐项填写额度。只有用户名、隐藏密码、NPC 对话原文和确实需要填写的数值需要键入。与 NPC 交谈会调用当前配置的真实 LLM。

首次注册时密码至少 8 个字符；长度不足或两次不一致会说明原因并原地重新输入。后端重启后，菜单会自动检查活动存档并提供“继续活动存档”，不要求玩家输入 `session_id`。

原始命令模式只保留给开发人员检查协议：

```powershell
python main.py --command-mode
```

普通玩家直接运行 `python main.py`，无需记忆 `register`、`new`、`choose`、`talk`、`end` 等命令。

完整协议见 [M2 终端文字协议](../../backend/docs/terminal_api.md)。
