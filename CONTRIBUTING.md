# Contributing

本项目主要说明提交规范和分支工作流，方便多人协作。

## 1. 分支工作流

- `main`
  - 主分支，仅用于已合并的稳定代码
  - 线上部署 / 最终版本所在

- 功能分支
  - 命名格式：`feature/<简短描述>`
  - 示例：`feature/story-branching`

- 修复分支
  - 命名格式：`fix/<简短描述>`
  - 示例：`fix/state-persistence-bug`

- 文档分支
  - 命名格式：`docs/<简短描述>`
  - 示例：`docs/contributing-update`

- 其他分支
  - `chore/<简短描述>`：工具、配置、依赖更新
  - `refactor/<简短描述>`：重构、代码结构调整

## 2. 提交规范

所有提交信息请遵循以下格式：

```text
<类型>: <简要说明>

<可选详细说明>
```

### 类型说明

- `feat`：新增功能
- `fix`：修复 bug
- `docs`：文档更新
- `style`：格式调整，不影响逻辑
- `refactor`：代码重构
- `test`：添加或修改测试
- `chore`：依赖、脚本、构建等维护工作

### 示例

```text
feat: 添加剧情节点分支逻辑
```

```text
fix: 修复 NPC 情绪未持久化的错误
```

```text
docs: 更新贡献指南
```

## 3. 分支使用流程

1. 从  `main` 拉出新分支
   - `git checkout main`
   - `git pull`
   - `git checkout -b feature/<描述>`

2. 开发完成后提交到自己分支
   - `git add .`
   - `git commit -m "feat: ..."`

3. 推送分支并创建 PR
   - `git push origin feature/<描述>`
   - 在 PR 里说明改动内容、目的和测试方式

4. PR 合并前
   - 确认无冲突
   - 代码通过基本检查
   - 如果需要，补充测试和文档

## 4. 其他建议

- 一次 PR 尽量聚焦一个主题
- 提交信息简明、可读
- 避免直接在 `main` 上开发
- 规范命名有助于后续维护