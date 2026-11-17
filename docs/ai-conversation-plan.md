# AI 会话实现计划

依据：已确认的 `docs/ai-conversation-design.md`。在当前会话内顺序实施；项目无 Git 仓库，不创建提交或工作树。

**目标**：各 AI 入口独立持久化历史，按任务读取最新先验，通过已确认信息协作。

**结构**：`services/conversations.py` 管作用域、存储、版本和并发；`services/ai_context.py` 管规则、先验与预算；`routers/conversations.py` 提供记录/材料接口；`web/js/conversation.js` 统一客户端会话操作。

- [x] 1. 在 `tests/test_ai_conversations.py` 编写接口与模型输入回归测试，运行 `.venv/Scripts/python.exe -m unittest discover -s tests -p test_ai_conversations.py -v`，确认现状不满足历史恢复、隔离与先验要求。
- [x] 2. `db.py` 增量建会话表；新增会话服务和接口。事务保存成功问答对，按版本拒绝清空后的迟到响应，材料只在所属作用域存取。测试论文 A/B、删除、失败和清空。
- [x] 3. 适配 `models.py`、`routers/ai.py`、`routers/categories.py`、`services/rag.py`、`services/category_assistant.py`。所有入口通过后端历史生成消息；只保留 user/assistant；原文与资料在资料区；研究画像与分类偏好逐次读取。
- [x] 4. 单篇正文补充问题相关片段及当前页；全库追问携带近期主题与来源 ID，来源每轮重新编号。区分摘要、生成内容和真实页码。针对后半篇、连续指代、笔记与批注运行回归测试。
- [x] 5. 新增客户端会话模块并以 Node 内置测试验证延迟返回、清空和切换。接入 `reader.js`、`chat.js`、`profile-chat.js`、`category-assistant.js`。持久化附件与独立清空，迁移画像 localStorage，旧草稿失效。
- [x] 6. 翻译、解释写入所属论文历史，添加页码和邻近文本；精读、相关性、技能与订阅翻译明确各自任务边界。
- [x] 7. 运行后端回归、Node 回归、Python compileall、JS 语法检查与 `tests/smoke.py`。全部使用临时数据和 mock LLM；更新使用说明与设计状态，逐项核对验收场景。

验证重点：模型实际收到的消息及来源、SQLite 持久化恢复、清空期间在途请求、论文切换时上传归属，而非仅检查提示词字符串。

## 完成记录

2026-10-27：步骤 1–7 均已完成。后端先验证缺失行为导致测试失败，再实现并通过；前端会话测试同样先失败后通过。

- 后端回归 28/28；Node 会话测试 5/5。
- Headless Edge 实际交互 7 组场景通过，无浏览器运行时错误。
- 原有冒烟脚本报告 20/20，其中未配置 LLM 错误路径按原脚本跳过；新增回归单独覆盖 LLM 调用失败不写入历史。
- Python compileall 与 13 个前端 JS 模块语法检查通过。
- 说明与交接已更新，AI 调用使用 mock 验证；未打包、未安装到真实用户环境。
