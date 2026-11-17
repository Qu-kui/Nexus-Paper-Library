# GitHub 同步

- 仓库：<https://github.com/Qu-kui/Nexus-Paper-Library>
- 默认分支：`main`
- 本地路径：`D:\Harness APP\PaperWhale`

## 一键同步

双击仓库根目录的 `sync-github.cmd`。脚本会：

1. `git add -A`
2. 有改动时自动提交（提交信息带时间）
3. `git push origin main`

也可以在终端运行：

```bat
sync-github.cmd
```

## 不会上传的内容

`.gitignore` 已排除：

- `data/`：本地文献库、设置、API Key
- `release/`、`dist/`、`build/`：构建产物和安装包
- `pw-debug/`：调试截图
- `单词本/`：个人单词数据
- `.venv/`、日志、临时 Office 文件

## 首次验证

```bat
git status
git remote -v
git log -1
```

SSH 使用 `~/.ssh/paperwhale_ed25519`，GitHub 上对应 key 名为 `PaperWhale sync`；通过 `ssh.github.com:22` 连接。
