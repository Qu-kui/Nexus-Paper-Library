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

## 安装包

- [NPL-2.24.16-Setup.exe](release/NPL-2.24.16-Setup.exe)（58.4 MB）
- SHA256：`5ff89c3d0e668d2122343f2f248f89417be0fa4b269ae581a88eeb3fd6dac7ef`
- 校验清单：[SHA256SUMS.txt](release/SHA256SUMS.txt)

## 不会上传的内容

`.gitignore` 默认排除：

- `data/`：本地文献库、设置、API Key
- `release/`、`dist/`、`build/`：构建产物和安装包（已手动加入仓库的安装包除外）
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
