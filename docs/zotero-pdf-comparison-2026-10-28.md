# Zotero PDF 获取源码对照与 ScienceDirect 修正

## 后续补强：2.5.1

2.5.1 安装包、便携版和 Connector 已生成，离线资源/模块/版本/技能及哈希校验通过；未自动安装。浏览器侧改动需要重载新版 Connector。

下文记录最初对照时的边界。后续已完成动态菜单读取/状态恢复、同 PII 代理页识别、实际来源和 User-Agent 保留、多候选有界重试、正文优先、请求来源策略，以及按主机间隔和 429/503/连续403冷却。原文签名不进入诊断。最终集成回归 Python 174 / Node 42 通过；原会话浏览器流程一次 PDF.js 传输错误后独立复跑通过。真实样例仍需有权的浏览器会话验证，不能用合成测试声称已成功下载该论文。

用户样例：`https://www.sciencedirect.com/science/article/pii/S0143816619313120`。

## 核查结论

研究环境访问样例得到 HTTP 403，尚未复现用户浏览器中 Zotero 成功保存的具体请求。以下是源码层面确认的差异，不代表该论文的真实 PDF 已经下载或订阅权限已验证。

查看的官方源码：

- [ScienceDirect translator](https://github.com/zotero/translators/blob/master/ScienceDirect.js)：页面专用识别、动态菜单、下载 JSON、pdfft 入口、中转页解析。
- [Connector itemSaver](https://github.com/zotero/zotero-connectors/blob/master/src/common/itemSaver.js)：附件通过浏览器 Connector 保存，带来源上下文；与仅用桌面客户端查找全文的环境不同。
- [桌面 attachments](https://github.com/zotero/zotero/blob/master/chrome/content/zotero/xpcom/attachments.js)：多解析器、失败后继续尝试、下载队列按域名延迟与连续失败控制。
- 同时浏览了桌面 file.js；实际站点适配主要位于 translator，而不是通用文件保存层。

## 已修改源码

1. `pdf_download.py` 新增 ScienceDirect 的 pdfLink、下载 JSON 参数识别、文章 pdfft 备用入口；解析 meta refresh 与 redirect-message。仍沿用请求数量、深度、超时、PDF 解析验证和按目标域匹配 Cookie。原有失败报告会去掉签名查询参数。
2. Connector 同样读取 ScienceDirect JSON 和按钮链接。优先尝试签名入口，缺失时使用正常文章下载入口；不编造 md5、pid 或机构凭据。
3. Connector 优先在当前标签页上下文获取附件，失败后尝试扩展上下文；下载遇到中转 HTML 时继续解析真实链接，不再直接放弃。共享单次 60 秒限制、最多 8 个地址、3 层解析、80MB PDF/2MB HTML 限制和循环检测。

这些是依据公开网页格式独立编写的兼容实现，没有将 Zotero 源码或运行时直接嵌入项目。

## 仍值得加强

- 动态菜单：部分页面必须点击下载按钮后才产生链接。可在用户点击 Connector 保存时短暂展开并读取菜单，保留原页面状态；目前只读取已存在的 DOM、JSON 和标准下载入口。
- 页面上下文和代理：浏览器页面、机构代理、Cookie、请求来源及验证状态需保持一致；从浏览器复制 Cookie 并不必然等价于该浏览器发请求。
- 按域名控制队列：借鉴 Zotero 的域名延迟和连续失败暂停，减少重复撞限流；现有短重试和总预算不能代替这一层。
- 全文与补充材料排序：优先正文 PDF，附件单独处理；当前不能只凭扩展名判断文件用途。
- 实际样例验证：需要在有访问权的同一浏览器页面里检查 Connector 实际跳转和获取报告，才能判断该论文是否还受到反爬、代理或会话限制。

## 验证与发布边界

新增合成页面回归覆盖签名参数、无 PDF 后缀按钮、中转刷新、相对链接、循环、错误 JSON、域名伪装以及真实 PDF fixture 的三段 HTTP 链路；Headless Edge 验证 Connector 元数据提取和带凭据的中转解析。测试不使用真实用户 Cookie、模型密钥或文献库。

当前改动在源码中，尚未生成新的安装包；已有 2.5.0 发布包未包含本次修正。

本轮完整隔离回归通过：Python 151 项、Node 37 项（含 Edge 浏览器解析与下载回归）。
