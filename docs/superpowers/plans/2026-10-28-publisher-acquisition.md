# 出版社 PDF 获取补强计划

> Agentic workers: use subagent-driven-development for independent backend and Connector tasks, then review integration and verify the release.

**Goal:** 完善用户已授权页面中的 PDF 动态入口和代理会话处理，限制同出版社重复失败，并生成 2.5.1 更新包。

**Architecture:** Connector 在用户点击保存时读取动态菜单，保留实际页面/代理来源，多候选共享时限。桌面解析器仍有流量、文件大小、深度和身份检查；新增按目标主机的请求间隔和冷却策略，权限拒绝按会话上下文隔离。已有 2.5.0 包保持不变。

**Tech stack:** Python/httpx/FastAPI、Chrome Extension MV3、SQLite、Node/Playwright、PyInstaller。

- [x] Connector：先写动态菜单、代理、多候选回归，再实现有界提取/下载，保留用户手动链接。
- [x] 后端：虚拟时钟测试域名间隔、429/Retry-After、连续拒绝、会话隔离、超时及成功恢复，再接入真实下载器。
- [x] 对照复核：确认 Cookie 和签名参数不进入诊断、不在跨域跳转泄露、正文优先，人工验证页不伪装成功。
- [x] 集成：运行完整 Python/Node 回归与隔离浏览器流程，不改真实用户库/邮箱/登录状态。
- [x] 发布：更新说明及版本 2.5.1，打包安装版、便携版、Connector，执行离线完整性/技能/编译工具/哈希验证，不自动安装。
