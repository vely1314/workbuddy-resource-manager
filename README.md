# WorkBuddy 个人工作台

一个本地运行的单页控制台，用可视化方式管理 WorkBuddy 的技能、文件、项目、记忆、个人资料、知识索引、回收站、连接器快照与主题风格。

服务只监听 `127.0.0.1:8765`，不对外暴露，不上传任何数据。

## 快速开始

**Windows：** 双击 `启动工作台.bat`，等待浏览器自动打开 `http://127.0.0.1:8765`。

脚本会按顺序自动寻找 Python：
1. WorkBuddy 自带的 Python（`%USERPROFILE%\.workbuddy\binaries\python\versions\`）
2. PATH 中的 `python` / `python3`
3. `py -3` 启动器

**手动启动：** 在 `workbench` 目录执行 `python server.py`，然后访问 `http://127.0.0.1:8765`。

## 环境要求

| 项目 | 要求 |
| --- | --- |
| 系统 | Windows 用 `启动工作台.bat`；其他系统直接 `python server.py` |
| Python | 3.10+，只用标准库，无需 `pip install` |
| 浏览器 | Edge / Chrome / Firefox 等现代浏览器 |
| WorkBuddy | 建议安装。技能、记忆、个人资料、主题同步都读取本机 WorkBuddy 数据 |

没装 WorkBuddy 也能启动，但上述页面基本为空；项目管理、文件管理、知识库仍可使用。

## 目录怎么放

后端把 `server.py` 所在目录的**上一级**当作工作区（`WORKSPACE`）。建议把 `workbench` 放进你自己的 WorkBuddy 工作区：

```
WorkBuddy/
└── my-console/
    └── workbench/
        ├── server.py
        └── index.html
```

这样"项目管理""文件管理"显示的就是 `my-console` 里的内容。

## 数据从哪来

| 页面 | 数据来源 |
| --- | --- |
| 技能管理 | `~/.workbuddy/skills` + 工作区 `.workbuddy/skills` |
| 记忆管理 | `~/.workbuddy/user-*-personal/MEMORY.md` + 工作区 `.workbuddy/memory/*.md` |
| 个人资料 | `~/.workbuddy/user-*-personal/{USER,IDENTITY,SOUL}.md` |
| 主题同步 | `~/.workbuddy/appearance-resources/theme-*/skin.css` 与 `storage/**/swr.json` |
| 连接器 / 定时任务 | `workbench/connectors.json`、`automations.json` 快照，需由会话桥接写入 |
| 知识库 | 工作区 `knowledge/` 目录，在页面点"重建索引"生成 `knowledge-index.json` |
| 回收站 | `config.json` 中配置的位置 |

用户目录 ID 和主题 scoped ID 都是运行时自动发现的，不用改代码。如果机器上有多个 WorkBuddy 用户目录，可用环境变量 `WB_PERSONAL_DIR` 和 `WB_CATALOG` 指定。

## 隐私与提交

以下文件包含本机路径和个人数据，**已写入 `.gitignore`，不要提交**：

- `config.json`（回收站路径、删除记录）
- `connectors.json` / `automations.json`（连接器与定时任务快照）
- `knowledge-index.json`（知识库正文）

仓库里只保留 `*.example.json` 空白模板。首次运行不需要这些文件，程序会自动用默认值；需要时在设置页配置，或把模板复制成对应文件名。

## 发布到 GitHub

在 `workbench` 目录执行：

```
git init
git add .
git commit -m "Initial commit"
git remote add origin <你的仓库地址>
git push -u origin main
```

提交前用 `git status` 确认没有上面列出的个人数据文件。

## 许可证

MIT License，详见 `LICENSE`。可以自由使用、修改和分发，但请保留版权声明与许可文本。

## 常见问题

- **端口 8765 被占用**：关掉旧的工作台服务，或改 `server.py` 里的 `PORT`。
- **显示"未检测到 WorkBuddy"**：本机没有 WorkBuddy 数据目录，技能/记忆等页面会是空的，其余功能正常。
- **连接器状态为空**：这是会话层快照，不会自动抓云端授权，需要 WorkBuddy 会话把结果写入 `connectors.json`。
- **改了代码没生效**：刷新浏览器；改了 `server.py` 需要重启服务。
