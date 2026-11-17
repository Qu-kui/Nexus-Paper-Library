# 发现论文精简与检索增强实施计划

> For agentic workers: use subagent-driven-development for independent modules and review the result before release.

**Goal:** 候选列表直接处理、中英题名、可恢复的话题删除、紧凑实时思考，以及随 exe 分发的检索增强。

**Architecture:** 保持来源元数据作为唯一论文证据。翻译只增加展示字段；话题删除归档到现有回收站，版本检查阻止迟到结果。内置检索规范进入规划和筛选提示词，公开接口由本机程序实际调用。

**Tech Stack:** FastAPI / SQLite / vanilla JS / httpx / PyInstaller。

- [x] 后端：先增加删除、恢复、迟到响应和翻译不改变用户处理状态的回归，再实现话题删除及旧候选题名补译。
- [x] 界面：隐藏重复结果清单，候选行显示中文题名；接入删除及补译；思考区总高度受限，跟随新内容并允许向上阅读。用隔离 Edge 流程验证。
- [x] 检索：加入 Europe PMC；发展脉络时对真实 OpenAlex 种子做有限前后向引用扩展。沿用日期、去重、取消、超时与来源警告。
- [x] 技能：编写应用内置 academic-discovery 规范，实际接入规划/筛选；筛选同轮返回中文题名，不改原文标识或链接。
- [x] 核查：审查需求与并发边界，运行后端、Node 和有关浏览器回归。
- [x] 发布：更新说明和版本为 2.5.0，打包安装版/便携版/Connector，验证代码、技能、资源及 SHA256；不自动安装。
