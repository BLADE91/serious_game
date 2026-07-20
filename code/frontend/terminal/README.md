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

进入后依次输入：

```text
register local-player
origins
new technical
choose A
end
choose C
talk opp_d02_wu_xiuying_first_talk 我想先听您说真话
knowledge
end
opportunities
map
review
validate
quit
```

首次运行先输入 `register <用户名>`，随后按隐藏提示输入两次密码（至少 8 个字符）；长度不足或两次不一致时客户端会说明原因并要求重新输入。注册成功后会自动登录。后续启动客户端使用 `login <用户名>`，再输入密码即可恢复该账号的存档。`whoami` 查看当前账号，`logout` 退出。密码不会显示在终端命令或命令历史中。

即可走完 D1–D3 教程切片，并继续用 `choose`、`talk`、`act` 和 `end` 推进至 D90。`map` 查看当前地点入口，`review` 查看玩家可见复盘，`validate` 查看已发布剧本包的完整性报告。客户端会在新建时打印 `session_id`。

后端默认使用 SQLite 存档。停止并重新启动后端后，输入 `continue` 可继续当前账号最近的活动存档；也可用 `load <session_id>` 指定存档。若显式设置 `GAME_REPOSITORY=memory`，进程退出后存档才会消失。

完整协议见 [M2 终端文字协议](../../backend/docs/terminal_api.md)。
