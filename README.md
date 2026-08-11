# 《浊流之上》网页美术版本 1.0

这是一个以县域搬迁治理为主题的严肃游戏。玩家扮演云溪县县长李致远，通过剧情阅读、NPC 会谈、调查行动、班子会议、行政文书、合同签订和夜间结算推进 90 天治理进程。

当前分支的玩家入口是网页，不再是旧终端客户端。

## 一、最省事的启动方法

适用系统：Windows 10 或 Windows 11。

### 1. 安装基础软件

开始前请确认电脑已经安装：

- Python 3.11 或更高版本；
- Node.js 22.13 或更高版本；
- Git（只有使用 `git clone` 下载时才需要）。

安装时请勾选 Python 安装程序中的 `Add Python to PATH`。安装完成后关闭并重新打开 PowerShell，然后执行：

```powershell
python --version
node --version
npm --version
```

三个命令都应正常输出版本号。Node.js 版本低于 22.13 时，网页无法正常构建。

### 2. 下载正确分支

推荐在 PowerShell 中执行：

```powershell
git clone --branch "网页美术版本1.0" --single-branch https://github.com/BLADE91/serious_game.git
cd serious_game
```

不使用 Git 时，也可以在 GitHub 页面切换到 `网页美术版本1.0` 分支，选择 `Code`、`Download ZIP`，下载后完整解压。

不要只下载 `code` 文件夹，也不要把 `BEGIN.BAT` 单独复制到别处。正确目录应当是：

```text
serious_game\
├── BEGIN.BAT
├── README.md
├── 最终剧本.md
└── code\
    ├── backend\
    └── frontend\web\
```

### 3. 配置真实 NPC API

GitHub 不会保存任何真实 API Key，因此每台电脑第一次运行前都必须单独配置。

可以在仓库根目录执行：

```powershell
Copy-Item code\backend\.env.example code\backend\.env
```

然后打开 `code\backend\.env`，至少确认以下配置：

```dotenv
GAME_ENVIRONMENT=sandbox
GAME_REPOSITORY=sqlite
GAME_DATABASE_PATH=data/serious_game.db

AUTH_COOKIE_SECURE=false
AUTH_REQUIRED=true
ALLOW_SELF_REGISTRATION=true

ROLE_LLM_PROVIDER=openai_compatible
ROLE_LLM_BASE_URL=https://api.qianzhang-ai.cn/v1
ROLE_LLM_MODEL=qwen3.6-plus
DOCUMENT_AUDIT_LLM_MODEL=qwen3.6-plus
CONTRACT_AUDIT_LLM_MODEL=qwen3.6-plus
ROLE_LLM_API_KEY_ENV=DASHSCOPE_API_KEY
DASHSCOPE_API_KEY=在这里填写你有权限使用的真实密钥

ROLE_LLM_FALLBACK_TO_FAKE=false
```

注意：

- 本地使用 HTTP，因此 `AUTH_COOKIE_SECURE` 必须是 `false`，否则可能出现登录后仍显示未登录；
- `ROLE_LLM_FALLBACK_TO_FAKE=false` 可以避免 API 调用失败时悄悄退回模板回答；
- API Key 只能保存在本机 `.env` 中，不得写进 README、源码或提交到 GitHub；
- 如果使用其他 OpenAI 兼容服务，请同时修改 `ROLE_LLM_BASE_URL`、模型名、Key 环境变量名和值；
- 项目曾在沟通中出现过明文密钥，正式共享前应撤销旧密钥并创建新密钥。

### 4. 一键启动网页

双击仓库根目录的：

```text
BEGIN.BAT
```

启动器会自动：

1. 检查 Python、Node.js、npm 和项目目录；
2. 首次运行时安装缺失的 Python 与网页依赖；
3. 在 `8100` 端口启动 FastAPI 后端，并开启热更新；
4. 在 `3001` 端口启动网页，并开启热更新；
5. 等待两端健康检查通过；
6. 自动打开 `http://127.0.0.1:3001`。

第一次安装依赖可能需要几分钟。启动成功后会保留两个命令行窗口：

- `Serious Game Backend - Hot Reload`；
- `Serious Game Web - Hot Reload`。

游戏运行期间不要关闭这两个窗口。结束游戏时关闭它们即可。

## 二、启动前自检

在仓库根目录打开 PowerShell，执行：

```powershell
cmd /c "BEGIN.BAT --check"
```

正常结果应包含：

```text
Check passed: Python, Node.js, backend, and web dependencies are ready.
Web address: http://127.0.0.1:3001
```

这个命令只检查环境，不启动服务。

## 三、确认游戏是否真正启动

启动后分别检查：

- 网页游戏：<http://127.0.0.1:3001>
- 后端健康状态：<http://127.0.0.1:8100/health/ready>
- 后端接口文档：<http://127.0.0.1:8100/docs>

健康接口应显示类似：

```json
{
  "status": "ready",
  "repository": "sqlite",
  "llm_provider": "openai_compatible"
}
```

如果 `llm_provider` 显示 `fake`，说明 `.env` 没有加载成功，或 `ROLE_LLM_PROVIDER` 配置错误。

## 四、常见问题排查

### 双击后启动的是旧终端版

你运行了仓库外或旧版本中的 `BEGIN.BAT`。网页版本必须运行当前仓库根目录的文件：

```text
serious_game\BEGIN.BAT
```

### 登录后没有反应或反复要求登录

检查 `code\backend\.env`：

```dotenv
AUTH_COOKIE_SECURE=false
AUTH_REQUIRED=true
ALLOW_SELF_REGISTRATION=true
```

修改后关闭后端窗口，重新双击 `BEGIN.BAT`。

### NPC 回答很机械或所有人说话相似

通常是没有配置真实 API，或者 API 失败后退回 Fake LLM。检查：

```dotenv
ROLE_LLM_PROVIDER=openai_compatible
DASHSCOPE_API_KEY=真实有效的密钥
ROLE_LLM_FALLBACK_TO_FAKE=false
```

然后查看后端窗口里的具体错误。常见原因包括余额不足、模型名错误、Base URL 错误、网络受限或密钥失效。

### 提示 Node.js 版本过低

执行：

```powershell
node --version
```

必须达到 22.13。升级 Node.js 后重新打开 PowerShell，再运行启动器。

### 提示找不到 Python

不要使用无法正常执行的 Microsoft Store Python 占位程序。重新安装 Python 3.11 以上版本，并启用 `Add Python to PATH`。

### 安装依赖失败

确认网络能够访问 npm 与 Python 包源。也可以手动执行：

```powershell
cd code\backend
python -m pip install -r requirements.txt

cd ..\frontend\web
npm install
```

### 端口 8100 或 3001 被占用

关闭之前启动的游戏窗口。如果仍被占用，在 PowerShell 中检查：

```powershell
Get-NetTCPConnection -LocalPort 8100,3001 -State Listen |
  Select-Object LocalPort,OwningProcess
```

确认进程属于旧游戏实例后，再通过任务管理器结束对应进程。不要随意结束不认识的系统进程。

### 网页显示“游戏服务暂时没有响应”

先打开 <http://127.0.0.1:8100/health/ready>。如果打不开，查看后端窗口最末尾的错误信息；如果能打开，再刷新网页。

### 修改代码后页面没有变化

确认网页窗口标题包含 `Hot Reload`，并且访问的是 `http://127.0.0.1:3001`。必要时按 `Ctrl+F5` 强制刷新。

## 五、手动启动方法

只有在 `BEGIN.BAT` 无法运行且需要定位问题时才使用。

先启动后端：

```powershell
cd code\backend
$env:GAME_REPOSITORY = "sqlite"
$env:GAME_DATABASE_PATH = "data/serious_game.db"
python run_server.py --reload
```

保持后端窗口开启，再新建一个 PowerShell 窗口启动网页：

```powershell
cd code\frontend\web
npm install
npm run dev -- --port 3001
```

然后打开 <http://127.0.0.1:3001>。

## 六、组员的数据与账号

- 本地运行使用 `code/backend/data/serious_game.db`；
- `data/` 已被 Git 忽略，不会上传个人账号和存档；
- 每名组员在自己的电脑上拥有独立数据库和独立进度；
- 拉取代码不会覆盖本地存档；
- 删除数据库文件会清空该电脑上的账号和游戏进度，操作前请备份。

## 七、获取后续更新

在仓库目录执行：

```powershell
git switch "网页美术版本1.0"
git pull
```

如果自己修改过文件，请先提交或备份，再执行 `git pull`，避免覆盖本地工作。

## 八、项目结构

```text
BEGIN.BAT                  # Windows 网页一键启动器
code/
├── backend/               # FastAPI 后端、SQLite/MySQL、剧本与 NPC LLM
└── frontend/
    ├── web/               # 当前网页玩家客户端
    └── terminal/          # 保留的开发测试终端，不是当前玩家入口
docs/                      # 产品、技术、玩法和美术资料
最终剧本.md                # 剧情内容权威来源
```

## 九、开发验证

网页：

```powershell
cd code\frontend\web
npm test
npm run lint
```

后端：

```powershell
cd code\backend
$env:PYTHONPATH = "src"
python -m pytest -q
```

终端兼容测试：

```powershell
cd code\frontend\terminal
$env:PYTHONPATH = "."
python -m pytest -q
```

## 十、进一步资料

- [最终剧本](最终剧本.md)
- [产品与技术资料](docs/product/)
- [后端说明](code/backend/README.md)
- [完整文档导航](docs/README.md)

如果启动仍失败，请把以下三项发给项目维护者，而不是只说“打不开”：

1. `BEGIN.BAT --check` 的完整输出；
2. 后端窗口最后 30 行；
3. 网页窗口最后 30 行以及浏览器中的错误提示。
