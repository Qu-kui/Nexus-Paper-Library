# PaperWhale 2.7.54 审查记录

日期：2026-11-01。范围：当前源码、主要页面、阅读器、导入、任务进度与大库性能。

本轮只做审查；新增本目录的报告、截图和隔离截图脚本，没有修改业务源码。页面检查使用临时 SQLite 库、233 条合成论文、模拟 AI 和独立无头 Edge，不使用真实文献库、邮箱、API Key 或用户浏览器配置。1500×1000 查看主要页面，1100×720 查看阅读器；捕获期间没有 pageerror。这不等同于完整回归通过，也没有验证真实出版商 PDF 下载。

## 应先修复的行为问题

| Before | After | Why |
| --- | --- | --- |
| 重导 PDF 时，仅凭数据库 file_path 非空就跳过补文件。文件实际已丢失也会报告 matched。 | 用统一的文件可用性检查；归档成功后才返回补齐成功。 | 临时库已复现：重导真实 PDF 后 action=matched，但 has_pdf=False。见 [ingest.py:230](../../paperwhale_server/services/ingest.py#L230)、[ingest.py:398](../../paperwhale_server/services/ingest.py#L398)。 |
| 同 DOI 的查重和插入不在同一原子操作中。 | 对规范化 DOI/arXiv 使用恰当唯一约束，并原子执行查重、插入与已有记录补全；标题模糊匹配独立处理。 | 临时库用 Barrier 固定两个合法并发请求的查重时序，得到两个 ID、两行记录。见 [ingest.py:341](../../paperwhale_server/services/ingest.py#L341)、[ingest.py:350](../../paperwhale_server/services/ingest.py#L350)。 |
| 阅读器方向键被通用捕获处理器和本地冒泡处理器各处理一次。 | 一个组件只保留一套标签键盘导航。 | 浏览器已复现：AI 精读按一次 ArrowRight，进入与我相关，跳过论文对话。见 [accessibility.js:47](../../web/js/accessibility.js#L47)、[reader.js:1677](../../web/js/reader.js#L1677)；原始结果见 observations.json。 |
| 阅读器会话更新先删除全部消息，再逐条渲染；每条都会滚到底部。 | 流式阶段只更新当前回复；用户原本接近底部时才跟随输出，上滚后提供回到底部按钮。 | 代码路径确认会打断查看旧消息、选区和焦点，长对话还会重复解析历史 Markdown。见 [reader.js:386](../../web/js/reader.js#L386)、[reader.js:395](../../web/js/reader.js#L395)。本项未做长会话端到端压力测量。 |
| 阅读器开启/关闭没有完整的焦点转移与返回，通用焦点管理器只覆盖 .modal。 | 打开时保存触发元素并聚焦阅读器，后台 inert，关闭时恢复焦点；按模态语义补齐标签。 | 代码确认后台控件可能仍参与 Tab 导航。见 [reader.js:1995](../../web/js/reader.js#L1995)、[reader.js:2062](../../web/js/reader.js#L2062)、[accessibility.js:15](../../web/js/accessibility.js#L15)。 |
| 任务状态只轮询最新批次，任务中心刷新还整体替换 DOM。 | 更新所有活动批次，按 batch_id 更新已有节点并保留详情展开态、焦点和滚动位置。 | 旧批次可能显示过期进度；下一轮刷新会折叠失败详情。代码确认，未做多批次完整端到端复现。见 [analysis_queue.py:68](../../paperwhale_server/services/analysis_queue.py#L68)、[pdf_fetch.py:125](../../paperwhale_server/services/pdf_fetch.py#L125)、[task-center.js:10](../../web/js/task-center.js#L10)。 |

## 性能与视觉可读性

| Before | After | Why |
| --- | --- | --- |
| 拖动阅读侧栏时，每个 pointermove 都写 localStorage 并触发所有页面容器重新布局、丢弃已有渲染。 | 用 requestAnimationFrame 合并尺寸预览；拖动结束保存宽度并做最终 PDF 重排；避免连续销毁画布。 | 静态调用路径明确；长文档有反复取消渲染和闪白风险，未测实际帧率。见 [reader.js:1654](../../web/js/reader.js#L1654)、[reader.js:1659](../../web/js/reader.js#L1659)、[reader.js:751](../../web/js/reader.js#L751)。 |
| 左下角剪贴板提示条是深底，但标题文字继承深色正文。 | 提示条使用独立的前景色、次级文字和按钮颜色，并适配主题。 | 截图与 computedStyle 已确认：背景 rgba(18,26,34,.94)，标题 rgb(16,32,58)，文字很难辨认。见 [features.css:4](../../web/css/features.css#L4)、[features.css:39](../../web/css/features.css#L39)，以及 01-library.png。 |
| 部分新主题的次级文字过浅。 | 保留色相，调低次级文字亮度，并验证正文、提示及选中态的对比度。 | 米白/青瓷/樱粉 ink3 在各自页面底色上的计算对比度分别为 3.59、3.75、3.88，表面上为 3.91、4.19、4.18；用于当前 11–13px 辅助文字时偏低。默认深海相同组合为 4.63/5.01。见 [theme.js:43](../../web/js/theme.js#L43)、[theme.js:48](../../web/js/theme.js#L48)、[theme.js:53](../../web/js/theme.js#L53)。这是本地颜色计算，没有进行完整 WCAG 审计。 |
| 每次翻页、搜索和刷新都请求全部标签和分类树；默认排序查询还要扫描和临时排序。 | 标签/分类按变更失效缓存；按实际常用排序补索引，再用大规模临时库衡量。 | library.js:763 发起三个请求；临时库 EXPLAIN 出现 SCAN papers 与 USE TEMP B-TREE FOR ORDER BY。本项属于规模增长优化，不能据此断言真实用户库已卡顿。 |

## 可以随后做的页面整理

以下是基于截图的设计建议，不是功能故障。

| Before | After | Why |
| --- | --- | --- |
| 没有 PDF 的详情仍保留高大的预览容器，将摘要压到下方。 | 有 PDF 时保留预览；无 PDF 时显示紧凑的获取/拖入区域。 | 02-detail.png 中大块空白没有提供额外信息，摘要需要继续滚动才能看到。 |
| 未配置邮箱时，发现论文页展开完整账号表单，占据大半首屏。 | 首屏保留简短连接提示，将账号表单放入可展开区域；AI 研究输入保持容易到达。 | page-discover.png 的 1000px 高视口中，候选论文区域刚好落在底部。两个模块仍独立保留。 |
| 方法/参数页中大量未提取论文各占一张空卡片。 | 未提取项目使用紧凑待办行，提取完成后展开为结果卡片；提供待提取/已提取过滤。 | page-methods.png 中主要空间被重复的“还没有提取”占据，跨论文找参数不够直接。 |
| 设置页九张卡片平铺，次级说明与常用连接设置竞争注意力。 | 增加分组导航或折叠区，保留每组保存与未保存状态，优先呈现模型、阅读和存储。 | page-settings.png 中首屏呈现六组局部内容；适合在修复行为问题后再整理。 |

建议顺序：先处理导入可信度、阅读对话滚动和任务反馈，再解决拖拽重绘、快捷键和颜色，最后整理页面。已有统一令牌、低动态模式与基本浮层动效；本轮没有依据建议整体换皮或增加列表入场动画。

## 附件

- [原始浏览器观察](observations.json)
- [文献库](01-library.png)、[详情](02-detail.png)
- [阅读器](03-reader.png)、[小窗口阅读器](04-reader-small.png)
- [发现论文](page-discover.png)、[方法/参数](page-methods.png)、[设置](page-settings.png)
- [知识库问答](page-chat.png)、[复习卡片](page-cards.png)、[导入](page-import.png)
- [可重跑的隔离截图脚本](capture-review.mjs)
