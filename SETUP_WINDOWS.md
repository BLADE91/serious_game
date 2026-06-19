# 剧本生成器 — Windows 安装使用指南

本指南假设你对 Python 完全不了解，每一步都有截图般的文字说明。跟着做就行。

---

## 第 1 步：安装 Python

1. 打开浏览器，访问 https://www.python.org/downloads/
2. 点击页面中间黄色大按钮，下载最新版 Python
3. 运行下载的安装程序（`.exe` 文件）
4. ⚠️ **重要：安装窗口底部有一个「Add Python to PATH」复选框，务必勾上**
5. 勾上后点击「Install Now」，等待安装完成
6. 验证安装：按键盘 `Win + R`，输入 `powershell`，回车，在弹出的蓝色窗口里输入：

```powershell
python --version
```

如果看到类似 `Python 3.12.x` 的输出，说明装好了。

---

## 第 2 步：把项目文件夹放到纯英文路径

你收到的项目包可能叫 `serious game-main` 之类带空格或中文的名字，需要改一下。

比如：

- ❌ `H:\E盘\谷歌下载\serious game-main`
- ✅ `H:\serious_game`

直接把整个文件夹**移动或重命名**即可。

---

## 第 3 步：创建虚拟环境 + 安装依赖

按 `Win + R`，输入 `powershell`，回车，然后逐条执行以下命令（每次一行，输完按回车）：

```powershell
# 1. 进入项目文件夹（把下面路径换成你实际的）
cd H:\serious_game

# 2. 创建虚拟环境（只需做一次，以后不用重复）
python -m venv venv

# 3. 激活虚拟环境
.\venv\Scripts\activate

# 4. 安装依赖
pip install -r requirements.txt
```

如果第 4 步出现类似 `Defaulting to user installation` 的提示，忽略就好，不影响。

成功标志：最后几行显示 `Successfully installed ...`。

> 💡 **以后每次重新打开 PowerShell 使用时，只要做第 1 步和第 3 步**（进目录 + 激活），不需要再创建虚拟环境和安装依赖。激活成功后命令行前面会出现绿色的 `(venv)` 字样。

---

## 第 4 步：放置 .env 配置文件

把单独发给你的 `.env` 文件放到项目文件夹里（和 `run_server.py` 同一级目录）。

---

## 第 5 步：启动服务

确保 PowerShell 里命令行前面有 `(venv)` 字样，并且当前在项目目录，然后：

```powershell
python run_server.py
```

成功标志：

```
INFO:     Started server process [xxxxx]
INFO:     Uvicorn running on http://0.0.0.0:8000
```

如果看到这两行，说明服务器已经跑起来了。

---

## 第 6 步：打开浏览器使用

打开浏览器（Chrome / Edge 都行），在地址栏输入：

```
http://localhost:8000
```

> ⚠️ **注意**：是 `localhost`，不是终端里显示的 `0.0.0.0`。`0.0.0.0` 不能在浏览器里访问。

回车，就能看到剧本生成器的 Web 界面了。

---

## 常见问题

| 问题 | 解决方法 |
|------|----------|
| `python` 不是内部或外部命令 | 装 Python 时没勾 "Add to PATH"。重新运行 Python 安装程序，选 "Modify"，勾上 "Add to PATH" |
| 打开网页显示无法加载 | 1. 是不是在浏览器输入了 `0.0.0.0:8000`？要输入 `localhost:8000`，这两个不一样<br>2. 服务器没启动。确认终端里看到 `Uvicorn running` |
| `pip install` 报大量红色错误 | 可能是公司网络限制。检查是否需要配置代理，或换手机热点试试 |
| `No module named 'xxx'` | 虚拟环境没激活。确认命令行前面有 `(venv)`，没有的话执行 `.\venv\Scripts\activate` |
| 页面打开了但点生成报错 | `.env` 里的账号密码没填或填错了 |
| 路径中文导致乱码 | 把项目文件夹移到纯英文路径，如 `H:\serious_game` |
| PowerShell 提示无法执行脚本 | 以管理员身份运行 PowerShell，执行 `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` |
