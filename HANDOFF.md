# PaperWhale 论文鲸 · 交接说明

## 用户确认的版本规则（2026-11-02，后续发布必须遵循）

- 正式版本使用「主版本号.次版本号.补丁号」，按改动性质递增，不按打包次数递增。
- 修复问题、小幅优化：只增加补丁号，例如2.8.0 → 2.8.1。
- 新增完整功能：增加次版本号，补丁号归零，例如2.8.1 → 2.9.0。
- 重大且不兼容的改版：增加主版本号，其余归零，例如2.x → 3.0.0。
- 内部测试、重复打包使用单独构建编号区分，不递增正式版本号；同一版本交付前的修正合并到该版本。
- 用户此次只要求记住规律，未要求重命名或重打已交付的2.7.68。保持现有产物与版本记录；下次新增完整功能按规则升至2.8.0，纯修复可为2.7.69。不要恢复历史上“每改一次/每打一次包，末位就加1”的做法。
- 以下为历史交接记录，其中旧的版本操作方式不覆盖本节最新规则。

> 给下一位接手优化的 AI / 开发者。读完这份再动代码，能避免大部分踩坑。
> 当前源码与发布包版本：**2.7.3**（Windows 桌面应用；2.5.2 之后又打了 2.6.x–2.7.3 多轮包，但 `docs/版本更新.md` 仍停在 2.5.2，下一版发布前需补齐用户可见说明）
> 交接时间：2026-10-28 晚（第二轮交接）

### 2026-10-28 晚 插件兜底抓取与 2.7.3 校验

- 本批重点仍是“网页论文 PDF 抓取”。真实库证据（%APPDATA%\PaperWhale\paperwhale.db，目标论文 b13e79eae1d74ba8850a02586efe2194）：19:58 那次保存的 `pdf_fetch_jobs.report.browser_attempts` 显示 `/pdfft` 与文章页都是“返回网页/网络失败”，不是 403；后台任务侧则全是 403 与 `domain_paused`。结论：用户已授权浏览器里 fetch 拿不到 PDF，新方向是“用浏览器自己下载”。
- 新增两条兜底路线（`extension/popup.js`）：`capturePdfViaHiddenIframe` 在已验证标签页注入隐藏 iframe，用 `webRequest.onHeadersReceived` 嗅探 `application/pdf`/attachment 后 `chrome.downloads.download` 落盘；`capturePdfViaTab` 复用当前已验证标签页导航再还原（新标签页会重新触发 Cloudflare/Turnstile），最多 3 候选、共享 90 秒预算。两条都失败才回退到 cookies + 后端。
- 新增 `extension/background.js`（MV3 service worker，manifest 已接）：弹窗“接住手动下载的 PDF”把 watch 存 `chrome.storage.local`，`downloads.onCreated` 接住用户自己点的 PDF 下载，完成后 POST `/api/import/web` 带 `pdf_download_path`。服务端 `_pdf_from_path` 只读复制（绝对路径、≥1KB、≤上限、`%PDF` 头校验）。
- 调试遗留（未清理，待确认是否保留）：`webimport.py` 每次导入把 `page_html` 写到 `DATA_DIR/sd-debug-page.html`；插件每次保存也发最多 2MB 页面 HTML。上线前建议去掉。
- 测试：修好 19:00 批留下的红测（`tests/pdf_extension.test.mjs` 旧断言不认新增的 iframe 注入调用），并新增 `tests/connector_watch.test.mjs`（background 监听 5 项）。Node 51 项全过（pdf_extension 10、connector_watch 5、publisher 4、conversation 5、reader_* 27）；PDF 相关 Python 78 项用 unittest 全过。注意本机 .venv 没装 pytest，用 `python -m unittest`。
- `tools/verify_release.py` 以前依赖 `dist/`（已 gitignore 且被清掉）会误报安装包 stale。现改为：dist 载荷在就比对、不在就直接用源树校验内嵌载荷，dist 程序不在就从便携包取同一份 exe。`--version 2.7.3` 现全部 PASS（54 后端模块、31 前端资源、全部技能、内嵌载荷、Connector 逐文件、SHA256）。产物：Setup 57,693,011 / 便携 50,554,853 / Connector 31,651 字节。
- 下面是必须用户配合的验证：重载 2.7.3 Connector → 打开目标论文 → 弹窗点“接住手动下载的 PDF” → 页面点 Download PDF → 看是否入库。没有这一步，不能声称真实样例抓取成功。
- 参考素材（未跟踪、不应入包）：`tests/.zotero-ref/`（Zotero 官方 translator/Connector 与 ScienceDirect 抓取脚本参考）、`tests/ScienceDirect.js`、`tests/.sd-page/page.html`。
- 2026-10-28 20:20–20:40 追加（真实用户复现后定位）：用户 Edge 里下载的 `pdfft (1~4).htm` 不是出版社拒绝，而是**插件自己的误判**——签名地址 `.../pdfft?md5=...&pid=1-s2.0-XXXX-main.pdf` 的查询串里带 `.pdf`，旧嗅探规则把它当 PDF 强制 `chrome.downloads.download`，于是把“Preparing your download”HTML 存成 pdfft.htm。修法：新增 `pdfEvidenceUrl()`，只认 `Content-Type: application/pdf|epub`、`Content-Disposition: attachment` + `.pdf`、或 **pathname** 结尾 `.pdf`；ScienceDirect 跳过隐藏 iframe（它只回 HTML 中间页）；标签页探测改成**永远新建后台标签页、用完即关**，不再导航用户当前文章页（用户明确要求像 Zotero 一样留在原页面）。回归在 `tests/pdf_extension.test.mjs`（11 项）与 `tests/connector_watch.test.mjs`（5 项）。
- 同一时段用户 Edge 下载目录 `D:\edge网页下载` 里其实**已经有一份真 PDF**：`1-s2.0-S0143816619313120-main.pdf`（19:07，1,472,244 字节，6 页，标题/DOI 一致）。已通过运行中 2.7.3 服务的 `/api/import/web` 带 `pdf_download_path` 归档到 `D:\论文鲸\论文文件\未分类\b13e79eae1d74ba8850a02586efe2194.pdf`，身份校验 state=matched、title_similarity=1.0、doi_found_on_first_page=true，paper.status=indexed、pdf_error 清空。**目标样例的真实 PDF 至此确实到手**（来源是早先一次真实下载，不是本轮自动抓取）。
- 之后再次抓取时 SD 返回 `init (2).htm`，标题 “Security verification” —— 说明本机/IP 已被出版社的安全验证拦截（连续自动请求触发）。结论：自动抓取要降频，且最稳的是“用户手动点 Download PDF + 插件接住下载”这条 watch 路线；不要在安全验证未过时反复重试，也不要把它当成插件代码失败。
- 已部署：修好的 `popup.js` 已复制到用户 Edge 实际加载的未打包目录 `D:\论文鲸\browser-extension\popup.js`（Edge 里 extension id klnfmjagbocfbold，location=4）。改动只在源码与发布物之间同步时记得两边都更新；旧文件备份在工作区 `backup-installed-popup-2.7.3.js`。

#### 2026-10-28 21:00–22:00 真实保存 422 定位与收尾

- **根因（用户真机证据）**：`/api/import/web` 返回 422，`loc=["body","pdf_base64"]`，`msg=Input should be a valid string`——前端把 `{attempts,base64,blocked}` 这个抓取诊断对象当 base64 发上去了。旧 popup 用 `new Error(data.detail)` 把 detail 数组 String 化，于是用户只看到 `[object Object]`（报错吞掉真因）。
- 修复（popup.js 归主 Bot / background.js 归「插件」Bot）：①`grabPdfBase64` 的 collect 只返回字符串；②`save()` 判 `typeof base64==='string'`；③错误统一走 `formatError`（字符串/Error.message/JSON.stringify/中文兜底）并带「[步骤] + HTTP 状态码 + detail JSON」；④background.js 自带同款 formatError（watch 链路遇 422 不再吞错误）。回归：`tests/pdf_extension.test.mjs` 20 项、`tests/connector_watch.test.mjs` 8 项，node 全量 67 项通过（变异回退可精确复现 `[object Object]`）。
- 诊断增强：`waitForPdfResponse` 遇到 `Content-Disposition: attachment` 且文件名是 .htm/.html/.txt 时提前结束并回调；`capturePdfViaTab(urls, deadline, attempts)` 把这类 URL 记成一条 `code=not_pdf, route=browser-extension`（复用服务端已有 code，不改 schema）。
- **当前 SD 抓取事实**：用户这次保存回传的页面（`%APPDATA%\PaperWhale\sd-debug-page.html`）里有签名链接 `pdfft?md5=fcd3e17c...&pid=1-s2.0-S0030399223009453-main.pdf`、有 `Full text access`、**没有**安全验证字样；但前端 fetch 该链接得到网页（缺 Referer/浏览器上下文的下载中间页），后台服务器请求则是 403 + `domain_paused`。结论：SD 的 /pdfft 依赖真实浏览器导航上下文，纯 fetch 拿不到。
- 待办（进行中，归「插件」Bot 评估）：在文章页同源注入 `<a download href=签名链接>` 并程序化 click，让浏览器自己带 Referer/Cookie 下载，再用 `downloads.onCreated` 接住并把非 .pdf 的误下载 erase；不确定点=程序化 click 是否被“自动下载”策略拦截、SD 是否仍回中间页。未经真机验证前不得声称自动抓取已通。
- 当前用户状态：保存链路已恢复（不再 422）；SD 那篇的 PDF 仍未自动抓到，watch（手动下载接管）是当下最稳路径。已安装 2.7.3 桌面程序不会热更新，服务端若要加防御需下个包。
- **Referer 路线已实测判死（2026-10-28 22:00，自动化）**：起 `tests/referer_probe/server.py`（127.0.0.1:8799）+ 一次性 auto-probe MV3 扩展，用 `msedge --headless=new --user-data-dir=<temp> --load-extension=<temp>` 跑 14 秒读服务端日志。结果：`chrome.downloads.download({headers:[{name:'Referer',...}]})` 直接抛 `Unsafe request header name`——Referer 是 Chromium **禁止设置**的请求头，API 层拒绝，不存在"静默丢弃"。对照组不带 headers 可发起下载（headless 下 state=interrupted/USER_CANCELED 与 Referer 无关）。推论：页面内同源 fetch 本就会自动带 Referer 而 SD 仍回 HTML，说明 SD 区分的是**导航请求 vs 普通请求**（Sec-Fetch-Mode 等），伪造 Referer 无效。`<a download>` 程序化点击同样不可行（跨源忽略 download 属性、同源只会拿到 pdfft 中间页 HTML、无 userGesture 且会触发自动下载拦截）。结论：ScienceDirect 这类站不要指望插件全自动下载；不要引入 `debugger` 权限做 CDP（侵入性高，未采纳）。
- 收尾细节：`discardDownload()` 会对非 .pdf 的误下载执行 cancel→removeFile→erase；found-path（嗅探命中但落成 .htm）note 一条 `not_pdf` 后直接 continue，不再白等竞速；`raceTimeout()` 会 clearTimeout 输家，修掉 popup/测试挂十几秒的 timer 泄漏。测试：node 全量 71 项通过（pdf_extension 24、connector_watch 8），extension 与 `D:\论文鲸\browser-extension` 同步一致（popup.js 49870 字节 / sha 5F880FB2，background.js 3414 / 9E44A64B）。
- 已实现（用户拍板加了）：保存时若既没抓到 base64 也没有本地下载文件，popup 自动 `armWatch()` —— 把轻量 meta（剔除 `page_html`/`pdf_base64`/`pdf_cookies`）通过 `chrome.runtime.sendMessage({type:'pw-watch-download'})` 挂进 background 的 watch，用户在网页上点一次 Download PDF 即自动入库（TTL 15 分钟）。风险已与用户确认：监听期内下载别的 PDF 会被拿到本篇元数据去入库，后端 identity 校验会拒绝不匹配的 PDF，只留元数据更新。测试：pdf_extension 26 项（含 arm 成功/失败两条 + light payload 断言）、connector_watch 8 项，全量 73 项通过；extension 与 `D:\论文鲸\browser-extension` 一致（popup.js 50957 字节 / sha 9A354210，background.js 3414 / 9E44A64B）。
- **DNR 补 Referer 方案（2026-10-28 22:20，自动化实测后实现，待真机验证）**：用户明确要求"像 Zotero 一样一次点击完成"。实测结论：①`downloads.download({headers:[{name:'Referer',...}]})` 抛 `Unsafe request header name`（Chromium 禁止）；②`declarativeNetRequest` 的 modifyHeaders **不作用于 downloads.download 发起的请求**（实测下载请求既无 referer 也无自定义头）；③但 DNR **作用于扩展自己的 fetch**（实测 /done 请求带上了 `referer` 和 `X-Probe`，Sec-Fetch-Mode: cors）。因此实现：manifest 增加 `declarativeNetRequest` 权限；popup.js 新增 `installRefererRule(pageUrl)`/`removeRefererRule()`，装一条 id=9001 的会话规则把 Referer 设为**文章页 URL**，作用域 `requestDomains:['sciencedirect.com','sciencedirectassets.com','elsevier.com']`，save() 的 PDF 阶段整体 `try/finally` 包住，无论成败都撤规则；无该 API 的旧浏览器安全降级。测试：pdf_extension 30 项（含规则装/撤生命周期、抛错仍撤、manifest 权限、旧浏览器降级），全量 77 项通过。同步后 popup.js 52845 / sha A368CE9B，manifest.json 921 / sha 406C28FE。**注意：真实 Edge 里 DNR 能否让 ScienceDirect 放行 PDF 尚未真机确认，用户重载扩展时需同意新增权限；未验证前不得声称自动抓取已修好。**
- **CDP 兜底（2026-10-28 22:40–23:00，本地自动验证通过，真机待确认）**：用户要求"一次点击就完成，不要手动点下载"。在 DNR 只能给扩展 fetch 补 Referer、SD 仍回网页之后，实现在 `capturePdfViaDebugger()`（popup.js）：manifest 增加 `debugger` 权限；`tabs.create({url:'about:blank',active:false})` → `chrome.debugger.attach(target,'1.3')` → `Fetch.enable({patterns:[{urlPattern:'*',requestStage:'Response'}]})` → `Page.navigate({url: 候选, referrer: 文章页URL})` → 在 `Fetch.requestPaused` 里按 content-type/URL 判定 PDF 后 `Fetch.getResponseBody` 取 base64 → finally 里 removeListener/detach/关标签页；所有 CDP 命令用 `raceTimeout` 限时（attach 8s、命令 10s、detach 5s），body 兜底定时器在 finish 里 clearTimeout。候选按 `/pdfft|\.pdf` 优先排序，最多试 2 个。
  - 本地自动验证（headless/windowed Edge + 临时扩展 + 本地探针服务）：`Page.navigate` 带 `referrer` 时服务端确实收到 Referer=文章页；`Fetch.getResponseBody` 拿到 `%PDF-` 正文（1236B）。**`Network.getResponseBody` 报 "No resource with given identifier found"、`window.PDFViewerApplication` 在无头/窗口 Edge 里都取不到**，不要走这两条。
  - 曾出现真 bug：`Fetch.requestPaused` 里提前 `settled = true` 导致 `finish()` 永不 resolve，正文被丢弃且白等 20s 超时（用户真机表现为"下载了一个东西被删除、却没抓到 PDF"）；已修并加注释。
  - 测试：pdf_extension 32 项（含 CDP 正文/attach-detach/候选排序/referrer 断言/定时器清零/无 debugger 降级/旧浏览器降级/manifest 权限），connector_watch 8 项，node 全量 **79 项通过（0 skip）**。同步：popup.js 58218 / sha 15203333，manifest 933 / sha 7B313720，background.js 3414 / 9E44A64B。
  - 风险与成本：`debugger` 权限较重，重载扩展会出现权限提示；抓取瞬间窗口顶部会闪一次"正在调试此浏览器"提示条。仅在本机使用，不上传任何数据。真机是否成功仍待用户重载后保存验证；未验证前不要在任何文档里写"已自动抓取成功"。
  - **真机成功（2026-10-29 10:41，目标样例闭环）**：用户在 Edge 重载后保存，弹窗显示「PDF 已下载并归档 / 调试通道：已捕获 PDF 正文（1993884 字节）」。数据库核对：`1cff2890e29a4eaf877c19a3aaab4130` 的 `file_path=D:\论文鲸\论文文件\光学工程\衍射光学\1cff2890...pdf`，1993883 字节、`%PDF-1.7`、status=analyzed。至此 ScienceDirect 这条"点一次保存就自动抓 PDF"的链路真机闭环（依赖 `debugger` 权限 + CDP Fetch 域 + `Page.navigate` 带 referrer）。
  - 真机验证后做的收尾：①PDF 阶段顺序改为 **CDP 调试通道优先**（真机证明它拿得到出版社只给真实导航的正文，且不会像标签页探测那样先落一个 init.htm 到下载目录），失败才回落隐藏 iframe / 后台标签页 / cookies；②`popup.html` 的「入库后让 AI 自动精读」改为**默认勾选**（用户要求）；③入库成功（`data.pdf === 'attached'`）时清空 `chrome.storage.local` 里的 `pwWatch`/`pwWatchResult`，避免下次打开弹窗看到过期的"接住失败"。回归：pdf_extension 35 项、node 全量 82 项通过；同步 popup.js 59797 / sha FFD26275，popup.html 1912 字节。
  - 已知小瑕疵：CDP 导航若让 Edge 顺手下载中间页，background 的 watch 可能接住一次失败下载并留下「接住失败：下载没有完成」提示；现在的清空逻辑只覆盖成功后，重复出现时再看是否需要给 background 加"仅接受 .pdf 完成态"的更严格过滤。

### 2026-10-29 导入去重（标题归一化）

- 用户问"Zotero 导入遇到重复能不能去重"。现状：`services/importers.py::_save_item` 先 `_find_existing` → `db.find_duplicate`，命中重复不新增行、只补全空字段并（缺 PDF 时）补 PDF，结果计入 `skipped`（前端 import.js 显示「跳过重复 / 无效条目」）。
- 已增强 `db.find_duplicate` 的标题匹配：新增 `db.normalize_title()`（NFKC 全半角归一 + 小写 + 去标点 + 压缩空白）与 `_title_prefix_match()`（短标题≥24 字符且≥4 词才允许"副标题前缀"匹配）。两道防误合并：①两边都有 DOI 且不同 → 不合并；②arXiv 同理；③短标题（<8 归一化字符）不做模糊匹配。顺带修了旧隐患：以前"标题完全一致但 DOI 不同"会直接合并，现在按更安全方向处理（宁可多一条待合并记录）。
- 注意：`strict_identifiers=True` 的路径保持原语义不变（发现论文新候选入库用）。性能：模糊匹配是 O(库内论文数) 的归一化扫描，只在前两级（DOI/arXiv/精确标题）都没命中时执行；库很大且批量导入很慢时再考虑加一列 title_norm。
- 测试：新增 `tests/test_title_dedupe.py` 8 条（标点/全半角/大小写/副标题/DOI 冲突/短标题/无关标题/strict 语义），与 test_pdf_acquisition、test_webimport_validation 合跑 39 条通过（隔离数据目录）。
- 生效条件：这是**服务端**改动，用户当前运行的是编译版 2.7.3，必须重新打包/安装（或从源码跑）才生效；未打包前不要对用户说已经生效。

### 2026-10-29 2.7.4 发布

- 版本号已同步到 6 处：`paperwhale_server/main.py` APP_VERSION、`web/index.html` 页脚、`installer/setup.py` DEFAULT_VERSION、`build.ps1` 参数与 usage、`extension/manifest.json`、`README.md`（4 处文件名）。`docs/版本更新.md` 新增 2.7.4 条目（原 2.5.2 条目降为二级标题）。
- 构建：`powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1 -Version 2.7.4`，日志 `build/`—实际日志在工作区 `build-2.7.4.log`（构建脚本本身不写 build/ 目录，注意别找错）。
- 产物（release/）：`PaperWhale-2.7.4-Setup.exe` 57,703,232 字节 / sha256 `4eaf908a…`；`PaperWhale-2.7.4-win-x64-portable.zip` 50,566,310 字节 / `28819d06…`；`PaperWhale-2.7.4-Connector.zip` 38,532 字节 / `1b8b0573…`。整包安装包比 2.7.3 大 10,221 字节（新增 debugger/CDP 与去重代码）。
- 离线校验 `tools/verify_release.py --version 2.7.4` **全部 PASS**：portable ZIP CRC、31 个前端资源、全部技能逐字节、安装包内嵌载荷与 dist 一致、54 个后端模块、Europe PMC/引用工具、PDF 域名协调与 referrer、Elsevier refresh 与浏览器诊断、Connector ZIP 与源码逐文件一致、SHA256SUMS 全匹配。
- 未自动安装（沿用本项目惯例）。用户安装 2.7.4 后，`D:\论文鲸\browser-extension` 会被新包替换，需要到 `edge://extensions` **重新加载**一次；2.7.4 的 Connector 自带 `debugger` 权限与 CDP 抓取逻辑。
- 遗留待办（未做）：2.6.0–2.7.3 的用户可见更新说明仍未补进 `docs/版本更新.md`（只有 2.7.4/2.7.5 条目）；如需补齐，必须先确认那些版本的实际改动，不要凭猜测写。

### 2026-10-29 2.7.5 发布（取代未安装的 2.7.4）

- 用户要求精简插件保存面板 + 「直接归档」＝**自己选归档路径**，实现后重新打包：
  - 插件 `popup.html`：只留 `#paper-title`（contenteditable，可就地改标题）+ 两个 radio（`save-mode=archive|analyze`，**默认都不选**）+ `#archive-path` 下拉（archive 时出现，选项来自 `/api/categories/tree` 的 `paths`，首项「未分类（默认目录）」）；其余字段收进 `#meta-fields.hidden`，仍完整抓取提交；去掉「接住手动下载」按钮（自动 arm watch 替代）。
  - 插件 `popup.js`：`syncTitleBox` / `chosenSaveMode`（无 `querySelectorAll` 时退化为 `archive`，保证测试/旧环境可用）/ `loadArchivePaths` / `toggleArchivePath`；`save()` 未选模式直接报错不提交；archive+选路径 → `payload.category_path = path.split(' / ')`。
  - 服务端：`WebImportIn` 加 `category_path: list[str]`；`webimport` 写进 meta；`ingest.create_paper_from_metadata` 在 `_insert_paper` 之后、`_attach_pdf` **之前**写入 `category_path/category_major/category_minor/category`（PDF 目录由 `categories.levels_of(paper)` 决定，顺序不能反）。新增 `tests/test_archive_path.py`（3 条：选定路径落对应目录、不选落未分类、模型接受字段）。
- 版本号六处同步到 2.7.5；`docs/版本更新.md` 增加 2.7.5 条目（2.7.4 降为二级标题）。
- 构建产物：`PaperWhale-2.7.5-Setup.exe` 57,707,171 字节 / sha256 `7eb10c0f…`；`PaperWhale-2.7.5-win-x64-portable.zip` 50,568,486 字节 / `ed6f4141…`；`PaperWhale-2.7.5-Connector.zip` 39,821 字节 / `a4619050…`。日志工作区 `build-2.7.5.log`。
- 校验：`tools/verify_release.py --version 2.7.5` 全 PASS；另从编译产物递归确认 `ingest.create_paper_from_metadata` 里引用了 `clean_levels`/`category_path`、`webimport` 模块含 `category_path`、`db.normalize_title` 仍在、`APP_VERSION=2.7.5`——不是只改了源码。
- 测试：node 全量 86 项（pdf_extension 39、connector_watch 8）、python 全量 205 项 + 新增 archive_path/title_dedupe。未自动安装。

### 2026-10-29 2.7.6 发布（自定义窗口框）

- 用户要求"像 Zotero 那样有自己的程序框，不要 Windows 默认标题栏"。实现：
  - `desktop.py`：`webview.create_window(..., frameless=True, easy_drag=True)`；新增暴露给前端的 `window_minimize / window_toggle_maximize / window_close / window_quit / window_toggle_fullscreen / open_guide`；`window_close` 复用既有 `_on_closing()`（收进托盘，不退出）；`window.events.shown` 里用 `ctypes` 给 frameless 窗口补回 `WS_THICKFRAME|WS_MAXIMIZEBOX|WS_MINIMIZEBOX`，否则会失去边缘缩放/最大化。
  - `web/index.html`：`<body>` 顶部新增 `#app-titlebar`（品牌 + `#tb-menus` + `pywebview-drag-region` 拖拽区 + 最小化/最大化/关闭），并新增 `/js/titlebar.js`。
  - `web/js/titlebar.js`（新文件）：只有检测到 `window.pywebview` / `pywebviewready` 才启用（浏览器模式不受影响）；五个菜单（文件/编辑/查看/工具/帮助）全部复用现有 `.nav-item[data-page]` 与既有按钮，不新增业务接口；双击拖拽区最大化；点空白/Esc 关菜单。
  - `web/css/style.css`：追加 `.app-titlebar` 系列样式；`body.desktop-shell` 下 `.sidebar/.main` 高度改成 `calc(100vh - 36px)`。
- 真机自测方式：用隔离数据目录运行 `desktop.py`，整屏截图确认标题栏/菜单/窗口按钮渲染正常，随后关闭进程并清理临时数据（未触碰真实库）。
- 版本号六处同步到 2.7.6；`docs/版本更新.md` 增加 2.7.6 条目。
- 产物：`PaperWhale-2.7.6-Setup.exe` 57,710,708 字节 / sha256 `04f8b5fc…`；便携 `50,572,492` / `e11439fa…`；Connector `39,821` / `c2f52eee…`。`verify_release --version 2.7.6` 全 PASS，**前端资源 32 个**（新增 titlebar.js）。日志工作区 `build-2.7.6.log`。
- 已知取舍：frameless 下窗口阴影/贴边分屏（Aero Snap）不如原生标题栏；若用户反馈边缘拖不动，再补 Win32 命中测试。菜单「使用说明」调用 `open_guide()` 打开随包 `使用说明.txt`。
- 最终回归（2.7.6 收尾，均为隔离/静态）：Node `node --test tests/*.test.mjs` **96 项通过**（pdf_extension 43、titlebar_ui 6、connector_watch 8）；Python `unittest discover`（`PAPERWHALE_DATA=tests\.tmp-data`、`PAPERWHALE_LLM_MOCK=1`）**208 项 OK，0 skip**；`tests/titlebar_ui.test.mjs` 6 条静态回归（frameless/easy_drag、窗口 API、index/css/titlebar.js 接线、verify_release 用 rglob 自动计数=32，含变异验证）。`repo extension/` 与 `D:\论文鲸\browser-extension` 15 个文件逐文件 sha256 全一致；2.7.6 manifest 933 字节 / `19FDE2EF…`（permissions 9 项）。真实 `%APPDATA%\PaperWhale` 在验证期间无写入。

### 2026-10-29 2.7.7 发布（修掉窗口灰边）

- 用户装了 2.7.6 后反馈"顶部还是有一条系统灰边"。根因：2.7.6 为恢复边缘缩放给 frameless 窗口补了 `WS_THICKFRAME`，frameless + 该样式会让 WinForms 用窗口背景色（#f4f6f9）绘制非客户区；且这种补法并没有真正恢复缩放。
- 修法：`desktop.py` 移除 `SetWindowLongW(style | ws_thickframe …)` 整段（不再改窗口样式）；新增暴露 `window_geometry()/window_move(x,y)/window_resize(w,h)`（基于 `window.x/y/width/height` 与 `move/resize`，最小 1040×700）；DWM 侧设置 immersive dark、`DWMWA_BORDER_COLOR`/`DWMWA_CAPTION_COLOR = 0x002B1A0A`（#0a1a2b）、圆角保留。`web/js/titlebar.js` 新增八条透明缩放手柄（`tb-resize-*`，pointerdown 取 geometry，pointermove 用 rAF 节流调 move/resize）；`web/css/style.css` 追加手柄样式与四类 cursor，且只在 `body.desktop-shell` 下显示。
- 产物：`PaperWhale-2.7.7-Setup.exe` 57,712,510 / sha256 `55ea9c7a…`；便携 `50,574,634` / `461e759b…`；Connector `39,821` / `c5dc5a6d…`。`verify_release --version 2.7.7` 全 PASS（32 前端资源）。
- 回归：`tests/titlebar_ui.test.mjs` 扩到 7 条，含**反向断言**（不得出现 `SetWindowLongW`/`| ws_thickframe`）+ DWM 属性与颜色常量 + 八手柄/四 cursor 断言；Node 全量 **97 项通过**、Python 208 项 OK。
- 待真机确认：灰边是否消失、八方向拖拽缩放是否顺手。若仍有浅色描边，下一步把 `DWMWA_BORDER_COLOR` 设为 `DWMWA_COLOR_NONE (0xFFFFFFFE)`，只保留圆角与阴影。

### 2026-10-29 2.7.8 发布（补齐 PDF 优化）

- 用户反馈"新加了很多论文，绝大部分抓不到 PDF"。实测库内 177 篇 / 53 有全文 / 124 缺；失败分类：限流 38、登录或人工验证 33、登录态被拒 18、直接拒绝 10、无开放全文 13、其余超时/不匹配/失效。另发现 `unpaywall_email` 为空导致 Unpaywall 查询被整体跳过（attempts 里 111 次 `source_configuration`），而用户已配置 `scholar_email`。
- 改动：
  1. `services/metadata.py`：新增 `_contact_email()`（环境变量 `PAPERWHALE_MAILTO` → 设置 `unpaywall_email` → `scholar_email` 依次回退）；`_get` 每次请求动态生成带 mailto 的 UA，并对 `api.openalex.org`/`api.crossref.org` 自动补 `mailto` 参数（礼貌池）。
  2. `services/pdf_fetch.py`：`_unpaywall_pdfs` 的邮箱同样回退到 `scholar_email`；`PdfFetchManager` 新增 `_schedule_retry/_requeue`——对 `rate_limited/domain_paused/needs_verification/timeout/network_error` 自动延迟重试一次（30–150 秒，Timer 守护线程），成功或永久失败时清空计数，`start()` 显式重置计数。
  3. `services/pdf_domains.py`：`RequestPolicy` 新增 `adaptive_spacing`（默认关闭，仅 `default_policy` 开）——429/403/带 Retry-After 的 503 后 `spacing_boost` 加倍（封顶 8s），成功后退避减半；这样既保留既有单元测试的精确时序语义，又让真实批量抓取自动降速。
- 测试：新增 `tests/test_pdf_acquisition_tuning.py` 8 条（自适应降速/封顶/403 降速、Unpaywall 邮箱回退与无邮箱跳过、礼貌池邮箱优先级、瞬时失败只重试一次/永久失败不重试）；全量 Python **216 项 OK**，Node exit 0（97 项）。
- 产物：`PaperWhale-2.7.8-Setup.exe` 57,716,121 / sha256 `f7719855…`；便携 `50,577,931` / `9979deef…`；Connector `39,820` / `5834eb63…`。`verify_release --version 2.7.8` 全 PASS；并从编译产物递归确认 `metadata._contact_email`、`pdf_fetch._schedule_retry`、`pdf_domains.adaptive_spacing`、`APP_VERSION=2.7.8` 都在包里。
- 边界（向用户说明过）：Optica/Nature/Wiley/IEEE 的硬性反爬在服务端层面无解，只能走浏览器插件或人工下载；如需"浏览器代抓批量"，要引入 debugger/CDP 控制用户 Edge，权限更重，需用户明确同意，未做。

### 2026-10-29 2.7.22 发布（EndNote 论文库迁移）

- 用户需求：把 EndNote 的论文库迁进 PaperWhale（用户说「endnote的论文库迁移也做一下」）。
- 背景事实：本机没装 EndNote、也没有 `.enl` 样本可做真机验证，所以走 EndNote 官方迁移格式 **XML 导出**（File → Export → XML + 勾选导出附件），这也是其它工具迁移 EndNote 的通用路线；`.enl` 是 EndNote 自有数据库格式，跨版本不稳，本版明确引导用户导出 XML。
- 实现：
  - `services/importers.py` 新增：`parse_endnote_xml()`（解析 `<records><record>`：`titles/title`、`titles/secondary-title`、`contributors/authors/author`（`Family, Given` → `Given Family`）、`dates/year`、`electronic-resource-num` → DOI、`urls/related-urls/url`、`urls/pdf-urls/url`、`abstract/p`、`keyword/keyword`）；`_endnote_data_dir()` 找 `<库名>.Data`；`_endnote_pdf_file()` 解析 `internal-pdf://<id>/<name>.pdf`（在 `.Data/PDF` 下按文件名/主干递归匹配，上限 4000）与 `file:///`；`find_endnote_source()` 文件夹内优先 xml → enl → ris/bib；`import_endnote_path(path, include_without_pdf=True)` 支持文件或文件夹，`.enl` 抛带操作指引的 ValueError。
  - `models.EndnoteIn(path, include_without_pdf=True)`；`routers/import_.py` 新增 `POST /api/import/endnote`，走 `_schedule_pdf_fetch`。
  - `desktop.py` 新增并注册 `pick_file()`（pywebview `OPEN_DIALOG`，过滤器含 `*.xml;*.enl;*.ris;*.bib`）。
  - 前端 `web/index.html` 新增「EndNote 论文库迁移」卡片（路径 + 选择文件… + 「没有 PDF 的条目也导入」勾选，默认勾选 + 开始迁移 + 结果框）；`web/js/import.js` 新增 `pickEndnoteFile()`/`importEndnote()`，结果里显示新增数、带 PDF 数、跳过数、可匹配附件数、后台补 PDF 排队数、失败明细。
  - 注意：迁移默认 `include_without_pdf=True`（迁移不该丢参考文献），与「导入 PDF 文件夹」的严格 PDF 门槛不同，用户可在卡片上取消勾选。
- 测试：新增 `tests/test_endnote_import.py` 8 条（XML 字段解析与附件解析、迁移带 PDF、`include_without_pdf=False` 只收有 PDF 的、二次迁移全判重、`.enl` 指引、空文件夹报错、API 端点成功/`.enl` 400）；新增 `tests/import_page_ui.test.mjs` 3 条（页面卡片与 id、`pick_file` 注册、import.js 请求参数与语法）。全量 Python **268 项 OK**，Node **130 项全过**。
- 产物：`PaperWhale-2.7.22-Setup.exe` 55,271,595 / sha256 `0e486fd51f28fdee5101a79c56e720925f1aca4349fcbd4407898369a0881a8c`；便携 `48,126,747` / `b1785b1a79127f4a467b80fd15752877f8d7a39d44c74d21c354e3da7ce95bfd`；Connector `43,688` / `7aa6080f3201d8744c3a3ce6532be10ad7a2f7efee250999aca56c0d20551d37`。`verify_release --version 2.7.22` 全 PASS。已静默安装（`/api/health` = 2.7.22；论文库 160 篇未变）。

### 2026-10-29 2.7.21 发布（Google Scholar 搜索结果批量入库）

- 用户需求：竞品（Zotero Connector / Mendeley Web Importer / EndNote Click）都能从 Google Scholar 一键入库，我们也要；前一轮对话确认了各家形态（见下），用户批准实现。
- 实现（全部在 `extension/`）：
  - `popup.js` 新增 `isScholarUrl`（匹配 `scholar.google.*`）、`extractScholarResults(limit)`（页面上下文自包含函数，解析 `#gs_res_ccl_mid .gs_r` → 标题去 `[PDF]/[HTML]` 前缀、`.gs_a` 拆作者/期刊/年份、`.gs_or_ggsm` 取 PDF 直链，最多 20 条、按标题去重）。
  - `init()` 分流：Scholar 结果页 → `initScholar()`，隐藏单篇表单、显示 `#scholar-panel`（默认全选，`全选/全不选/批量入库（N）`）。
  - `importScholar()`：逐条 `POST /api/import/web`，`download_pdf:false`（不逐篇同步下载，避免卡住），统计新增/已存在/失败；随后 `POST /api/pdf-fetch/start {paper_ids:[新 id]}` 只把**这一批**排队补 PDF（不会触发全库补齐）。
  - `popup.html` 新增 `#scholar-panel/#scholar-list/#scholar-count/#scholar-select-all/#scholar-select-none/#scholar-import/#scholar-status`；`popup.css` 配套样式（列表 max-height 320 可滚）。
- 竞品事实（本轮查证，供以后写文案用）：Zotero Connector 支持 Google Scholar 结果页整页保存；Mendeley Web Importer 官方文档即用 Google Scholar 演示，可勾选多条导入；EndNote 走 Scholar 的「Import into EndNote」(.enw 再导入) 或 EndNote Click 扩展推送到 EndNote Web。Google Scholar 无开放 API 且禁止自动抓取，所以各家都是「用户手势触发的一次性保存」，做不到后台自动同步。
- 测试：新增 `tests/scholar_import.test.mjs` 4 条（解析结果页字段；Scholar 页打开即全选并隐藏单篇表单；批量导入逐条 POST + 只排这批 PDF；全不选时拦截并提示）。全量 Node **127 项全过**，Python **260 项 OK**。
- 产物：`PaperWhale-2.7.21-Setup.exe` 55,261,219 / sha256 `e981e3f60c6770ddbf460c74b091612b0fb416639ebd2bbb3d924e3940a96a23`；便携 `48,118,861` / `37cb80706f38cf946816c8bfeb0ec2aa1bacece54eac4a8b05ef20a571b5b421`；Connector `43,688` / `db6d0f83dc7e511d67aa3fe04d31c600eeda08b2a3c225709b08e63afeaf41be`。`verify_release --version 2.7.21` 全 PASS。已静默安装（`/api/health` = 2.7.21），extension 四个改动文件已同步到 `D:\论文鲸\browser-extension`（需在 `edge://extensions` 重新加载 Connector）。

### 2026-10-29 2.7.20 发布（抓 PDF 时识别验证页 / 跟随跳转页）

- 用户贴出抓取失败日志：ScienceDirect PII `S0030401819300987`，`/pdfft` 与 `pdf.sciencedirectassets.com/.../main.pdf` 都是 200 + **52935 字节 HTML**（调试通道记录「头部 PCFkb2N0」→ base64 解码为 `<!doct`，即 HTML 文档），最后只能提示用户手动点 Download PDF。
- 根因：`capturePdfViaDebugger` 只判断「base64 是否 JVBER 开头」，非 PDF 就记一条 `not_pdf` 继续换候选；遇到出版社的安全验证页（Cloudflare/Elsevier 风控页）会白跑完整个超时，用户也看不懂发生了什么。另外 `/pdfft` 回 `text/html` 时（跳转页）根本不会去读正文。
- 实现（`extension/popup.js`）：
  - `decodeBase64Head()` 解码正文头部；`captureIsVerificationPage()` 命中 `security verification / just a moment / checking your browser / captcha / 请完成验证 / 人机验证 / 验证码 / unusual traffic …` 等标记 → 记 `needs_verification`、置 `state.pdfBlocked`、`state.debuggerNote` 说明，并**立即停止**后续候选（不再空转）。
  - `captureHtmlRedirect()` 从 HTML 里找 `location.replace(...)` / `location.href=` / `meta refresh`，命中就跟随跳转继续抓（最多 2 跳）。跳转地址用 `new URL(target, 当前响应 URL)` 解析。
  - 触发条件从「content-type/URL 像 PDF」扩展到「也包含 `/pdfft` 端点」，这样下载端点的跳转页能被读到。
  - 复用已有 UI 提示：`state.pdfBlocked` 已渲染「出版社要求安全验证：请先在浏览器里完成验证，或用页面上的 Download PDF 手动下载…」。
- 服务端无需改动：`pdf_trace.MESSAGES` 早已包含 `needs_verification` 且 sanitize 白名单放行。
- 测试：`tests/pdf_extension.test.mjs` 新增 2 条——(a) 验证页 → `needs_verification`、不再尝试第二个候选（`Page.navigate` 只 1 次）、定时器清空、UI 提示含「安全验证」；(b) 下载端点回跳转页 → 跟随到签名 PDF 并成功取到 base64、`Page.navigate` 包含跳转目标。顺带修了测试夹具：补 `atob`、支持「按第几次导航给不同响应」(`debugResponsesNext`)、`state.tab.url` 跟随 `meta.url`（否则换 PII 的用例会误报 Article changed）。全量 Node **123 项全过**，Python **260 项 OK**。
- 产物：`PaperWhale-2.7.20-Setup.exe` 55,262,681 / sha256 `560ed417f424ffe1be22e3524c854d155d182820592fb245f06a18c788e0b20c`；便携 `48,117,111` / `a13e05c07e6dd3170307e11450b494c5d5a35a9f22108d88316f0ed03b94a4f8`；Connector `40,869` / `e35dafe872dc34ec2ce7871d5b14863574e52c021a2e7839eca30b4408e7d3dd`。`verify_release --version 2.7.20` 全 PASS。已静默安装（`/api/health` = 2.7.20），并把 `extension/popup.js` 同步到 `D:\论文鲸\browser-extension`（需在 `edge://extensions` 重新加载 Connector 才生效）。

### 2026-10-29 2.7.19 发布（「–」最小化到任务栏，不再直接收进托盘）

- 用户报障：点标题栏的「–」直接收到右下角托盘，而不是缩到任务栏，要求优化。
- 根因：`desktop.py` 里给 `window.events.minimized += _on_minimized` 挂了 `window.hide()`（1.x 时代为「最小化也常驻托盘」的设计）；而自定义标题栏的 `window_minimize()` 调的是 `window.minimize()`，最小化事件一触发就被 hide，于是视觉上直接从屏幕消失。
- 修复：删掉 `events.minimized` 的 hide 监听（保留注释说明约定）。现在：
  - 「–」= 正常最小化到任务栏（`IsIconic=True`，窗口仍可见）
  - 「×」= 收进系统托盘常驻（`_on_closing` 保留 hide + 首次提示），托盘菜单「退出 PaperWhale」才结束进程
- 测试：`tests/titlebar_ui.test.mjs` 新增 1 条——禁止 `events.minimized +=`、`window_minimize()` 必须调 `window.minimize()` 且不许 `window.hide()`、同时 `events.closing` 与「已收进托盘」提示必须保留。
- **真机验证（dist 2.7.19，Win32 读窗口状态）**：点「–」后 `IsIconic=True`、`IsWindowVisible=True`、进程存活（最小化到任务栏）；恢复后点「×」→ `IsWindowVisible=False`、进程仍存活（收进托盘）。脚本 `pw-debug/e2e_minimize.py`。
- 测试基线：Python **258 项 OK**，Node **121 项全过**。
- 产物：`PaperWhale-2.7.19-Setup.exe` 55,257,708 / sha256 `44cf4b2f65b38ad110819f80512b367c9a0a6d4d05ff2862bd02ff78311b4c8d`；便携 `48,113,616` / `02fe76d98902f961bb3120c3f68a1bf916b30b2abded1d67668386694fa857ab`；Connector `39,822` / `70aa2eece4861ccdb6951641de568aba29235ab43156b6cca93d7c1aacdace46`。`verify_release --version 2.7.19` 全 PASS。已静默安装（`/api/health` = 2.7.19）。

### 2026-10-29 2.7.18 发布（脏标题也能按原文纠正）+ 真实库重复条目合并

- 2.7.17 只处理「同一篇的两种写法」，但真实库还有一类脏数据：**标题就是期刊名**（Zotero 导出里常见，如三条 `Optics Express`、一条 `IEEE Transactions on Instrumentation and Measurement`），这类标题与原文标题「完全不是同一篇」的写法，旧护栏会拒绝覆盖。
- 2.7.18 增加「原文证据」判定（`ingest._confirm_basic_metadata`）：
  - `_first_pages_text()` 取该论文开头几块正文（PDF 第一页一般就是标题/作者/期刊）；
  - 替换条件：新标题能在原文里逐字找到 + 旧标题像期刊名/占位词（`_title_looks_generic`：≤4 词或与期刊名相同）+（旧标题不在原文里 **或** 旧标题等于期刊名）；
  - 旧标题本身能在原文里找到的正常标题一律不覆盖，只记 `metadata_warnings`。
- 测试：`tests/test_analyze_metadata.py` 增至 7 条（新增「标题=期刊名 → 按原文纠正」「旧标题在原文里 → 不覆盖」）。全量 Python **258 项 OK**，Node **120 项全过**。
- **真实库重复条目合并（用户确认「全部 20 对都合并」后执行）**：
  - 脚本 `pw-debug/merge_duplicates.py`（默认 dry-run；执行前备份 `paperwhale-before-merge.db`）。规则：标题归一化（含去冠词）同篇且 DOI 不冲突；保留优先级 DOI > PDF > 摘要 > 元数据更全；标题取「能在 PDF 原文里逐字找到」的最长写法；摘要/要点取 `updated_at` 更新的一份；空字段互补；不移动磁盘 PDF；被丢弃条目走 `recovery.archive_paper`（回收站可恢复）。
  - 结果：合并 **20 对**，论文 187 → **160**，回收站 40 条。目标论文 `741ac7b6…` 现在是 `An Optical Frontend for a Convolutional Neural Network` + DOI `10.1364/AO.58.003179` + Applied Optics 2019 + 最新精读摘要（1060 字）。
  - 合并后仅剩 1 组同名但**不同 DOI**的条目（三条 `Optics Express`，是不同论文的脏标题），按设计不合并；装 2.7.18 后各跑一次精读即可按原文纠正标题。
- 产物：`PaperWhale-2.7.18-Setup.exe` 55,258,820 / sha256 `392ea6db862618eb702b61f9b30208e9ed5296ce9c66900769cd20327134a2e5`；便携 `48,115,400` / `4860cd7d8d7ee70ecd2a6a0ef629e20ea90d1b830554e2dd07db72823eae71d6`；Connector `39,821` / `77290cb8acaf9abee58033a079aee84e492315b1ad1c211025c2df0eabab452f`。`verify_release --version 2.7.18` 全 PASS。已静默安装（`/api/health` = 2.7.18）。

### 2026-10-29 2.7.17 发布（精读后按原文核对基础信息 + 冠词去重）

- 用户报障：「An Optical Frontend for a Convolutional Neural Network」精读后识别出的论文名错误。用户期望：精读读了全文，标题/作者等信息应该能准确提取，并在精读后校正一遍基础信息。
- 取证（只读查真实库）：该论文在库里有**两条**记录——
  - `741ac7b6…`：source=zotero，标题 `Optical frontend for a convolutional neural network`（Crossref 元数据写法，**没有 An**），DOI 10.1364/AO.58.003179，Applied Optics 2019，有 PDF + 摘要（10-27 精读）。
  - `be45f89b…`：source=folder（10-29 13:44 文件夹导入），标题 `An Optical Frontend for a Convolutional Neural Network`（PDF 首页印刷写法），有 PDF + 摘要（10-29 17:31 精读）。
  - 两个 PDF 的第 1 页 chunk 完全相同，确认是同一篇；Crossref 与 Optica 元数据都写作不带 An，PDF 印刷标题带 An。
  - 结论：**精读本身不写标题**（旧代码只补空的 authors/venue/year），标题差异来自两次导入；旧去重逻辑因开头冠词不同（`an optical…` vs `optical…`）没合并，于是同篇两条。
- 实现：
  - `ingest._confirm_basic_metadata(paper, data)`：精读时让模型从正文逐字读 `title/authors/venue/year/doi`（`_analyze_system_prompt` 增加 `title`/`doi` 两个字段并要求照抄、不翻译），然后按护栏写库：
    - 标题：只有 `_same_paper_title`（`db.normalize_title` → `db._drop_leading_article` → `_title_prefix_match`，忽略冠词/标点/大小写/副标题）为真才替换；否则不写并记 `metadata_warnings`（防错配 PDF 把别的论文标题写进来）。
    - 作者：只有分号分隔人数更多才替换（不会被更少的覆盖）。
    - 期刊/年份/DOI：空着才补。
  - `analyze_paper` 返回增加 `metadata_updates`（实际改了哪些字段的中文标签）与 `metadata_warnings`；`web/js/reader.js` 精读完成提示改为「精读完成，已按原文校正标题、作者」。
  - **去重**：`db._drop_leading_article()` 在归一化匹配时去掉开头 a/an/the（仅当去掉后仍 ≥24 字符且 ≥4 词，避免把 `Editorial` / `An Editorial` 硬合并）；`find_duplicate` 的模糊分支两边都先过这一步。
- 测试：新增 `tests/test_analyze_metadata.py` 5 条（原文标题校正为带 An 的写法、空字段补全、模型给别的论文标题不写入且留警告、作者不被降级、已有期刊/年份/DOI 不被覆盖）；`tests/test_title_dedupe.py` 新增 3 条（An/The 冠词差异算重复、短标题仍不硬合并）。全量 Python **256 项 OK**，Node **120 项全过**。
- 遗留数据问题：真实库里按新规则还有 **20 对**同篇重复（大多来自 10-29 11:32 那次 Zotero 导入，例如 `Fast fourier…` vs `Fast Fourier…`、`Optical frontend…` vs `An Optical…`）；已做 dry-run 清单，等用户确认后合并（保留有 DOI + PDF 的一条，另一条走回收站）。
- 产物：`PaperWhale-2.7.17-Setup.exe` 55,257,374 / sha256 `ce51a3b81be7ae73ec0adefd2e53dbe97491d6f6f38255c3da9ec68d2bbf002a`；便携 `48,113,346` / `58ce432fea14bcba14e67997ec1f188bc253224d4c042c8dbde0227b0b33db47`；Connector `39,822` / `429d90339be06f90a664db0792ff8bf87e441629a4075b941029dc56917c0325`。`verify_release --version 2.7.17` 全 PASS。已静默安装到 `D:\论文鲸`（`/api/health` = 2.7.17）。

### 2026-10-29 2.7.16 发布（阅读器顶部一栏被标题栏盖住）

- 用户报障：阅读器（PDF + AI 精读面板）里，面板变宽/窗口变宽时顶部一栏被盖住，看起来是一排白色半截按钮。
- 根因（CDP 实测量到）：`.reader { position: fixed; inset: 0 }` 从视口顶部开始铺，而桌面模式的 `.app-titlebar` 是 `position: fixed; top:0; height:36px; z-index:9000`。阅读器里 `.reader-head`（min-height 54px，标题 + 工具按钮）`top = 0`、`height = 55`，被标题栏压掉 36px，只剩底部约 19px 露出来——就是用户看到的「白色半截按钮」。注：`reader-enhancements.css` 里 `#reader .reader-head { height:auto; flex-wrap:wrap }`，头部会随宽度换行变高，所以看起来和「面板宽」相关。
- 修复（`web/css/style.css`）：
  - `body.desktop-shell .reader { top: 36px; }`（同时得到 `height = 100vh - 36px` 的布局），浏览器模式仍用 `inset:0`；
  - `body.desktop-shell .modal { top: 36px; }`，避免高弹窗顶进标题栏；
  - `web/js/reader.js` 划词工具条 `minTop`：桌面模式下限 8 → **44px**，选中靠顶部的内容时工具条不会藏到标题栏背后。
- **逐页扫过一遍（用户要求「其他地方的类似问题」）**：隔离实例 + CDP 遍历 文献库 / 知识库问答 / 发现论文 / 导入 / 设置 / 阅读器 / 分类助手弹窗，检查「任何 `position:fixed` 且 `top<35 && bottom>40` 且宽>150 高>60 的层」：
  - 修复前：`#reader`（top 0, h 1080）压标题栏；其余页面无。
  - 修复后：全部为空，`reader-head top = 36`；分类助手弹窗 top=90、不跨标题栏。
- 顺带核实用户说的「文章里拖不动词」：在 PDF textLayer 上实测拖选**是能选中的**（5 行分别选中 21–27 字），窗口位置在拖动前后完全不变（2.7.15 的 easy_drag 修复生效）；之前「连着面板一起拖动」正是 easy_drag 导致整窗被拖。已在回复里请用户再试一次并说明具体位置。
- 测试：`tests/titlebar_ui.test.mjs` 新增 2 条静态回归——`body.desktop-shell .reader` 必须 `top:36px`（且 `.reader` 基础规则仍 `inset:0` 供浏览器模式）、`body.desktop-shell .modal` 同样下移；reader.js 划词工具条必须有 `desktop-shell ? 44 : 8` 的下限。
- 测试基线：Python **248 项 OK**，Node **120 项全过**。
- 产物：`PaperWhale-2.7.16-Setup.exe` 55,254,397 / sha256 `a086c7070ec34c9b6e788762058c68223a9e72b7ca571e7907e63c32a401a9d0`；便携 `48,109,360` / `19c74a5754ddf09acfb0e6ba6ca5dafbe69e99684e57a6f8949271f4f602a592`；Connector `39,822` / `883b81d01c459010a36bacb53278a7f1ffd0ad26a08a26521cbd05982d04eeb8`。`verify_release --version 2.7.16` 全 PASS（33 个 web 资源、15 个 skills、1182 个文件）。
- 已静默安装到 `D:\论文鲸`（自动关掉 2.7.15 再启动），`/api/health` = 2.7.16；从**已安装版**复测 `#reader` top = 36，并截图 `reader-2.7.16-fixed.png`（工作区）确认工具栏完整可见。

### 2026-10-29 2.7.15 发布（修「正文划词时整窗被拖走」）

- 用户报障：知识库问答页在正文里拖动划词，整个程序框跟着鼠标一起被拖动。
- 根因（读 pywebview 注入脚本 `webview/js/customize.js` 确认）：`easy_drag=True` 时会执行
  `window.addEventListener('mousedown', onMouseDown)`——**整页任何位置按下鼠标都开始拖窗口**。
  于是选中文字的动作变成了拖窗口。
- 修复：`desktop.py` 的 `easy_drag` 改为 `False`。pywebview 的 `document.body` 级拖拽区域监听
  （`.pywebview-drag-region`）与 easy_drag 无关、始终注册，所以标题栏仍可拖窗口，只是正文不再触发。
- 测试：`tests/titlebar_ui.test.mjs` 把断言从 `easy_drag=True` 改成必须 `False`（并明写原因），
  另加一条钉住「标题栏仍保留 ≥2 个 drag region（品牌区 + #tb-drag）」。
- **A/B 真机验证（CDP 合成鼠标，正文拖动）**：
  - 旧版（已安装 2.7.14，easy_drag=True）：在正文按下拖动 240px → 窗口从 `(130,130)` 跑到 `(588,-53)`，**复现用户 bug**。
  - 新版（dist 2.7.15，easy_drag=False）：同样操作 → 窗口 `(208,208)` 丝毫未动，且选中 30 个字符，**划词正常**。
  - 脚本 `pw-debug/ab_text_drag.py`（带 exe 路径与端口参数，可复跑）。
- 踩坑记录（重要）：用 CDP `Input.dispatchMouseEvent` 测「拖标题栏移动窗口」不可信——窗口一移动，
  视口坐标对应的屏幕位置就漂移，量出来是假象（老版同样异常）；用 Win32 真鼠标做同样的测试也因
  前台窗口/窗口重叠而结果不稳定。结论：拖窗口这段没有可靠自动化验证，代码路径与 2.7.14 相同
  （同一个 pywebview body 级 drag region 处理器），只去掉了「任意位置都能拖」这一层；
  已请用户真机确认标题栏拖动是否仍顺手，若不顺手则改为自己用 pointer 事件 + window_move 实现。
- 测试基线：Python **248 项 OK**，Node **118 项全过**。
- 产物：`PaperWhale-2.7.15-Setup.exe` 57,739,265 / sha256 `72d66e8fef84dd6d822851d6b5121e35ce1676380f0b458161050eef31604480`；便携 `50,597,499` / `9c6ece39fc783b928507f02a605e0ad618fca9c86df6fafbf41ca2767d191ee9`；Connector `39,822` / `ff7dca9dcb27c98d7d53434b387780c37a00a95e0d1607632391fc30d6dae547`。`verify_release --version 2.7.15` 全 PASS。

### 2026-10-29 2.7.14 发布（分类筛选改成逐级进入）

- 用户报障：文献库「全部分类」下拉把所有完整路径（`人工智能 / 光神经网络` 这种）平铺成一长串，几百条没法翻。要求「先显示第一级，点某一级再显示第二级」。
- 实现：
  - 新增 `web/js/category-picker.js`：树形层级选择器。纯函数 `buildCategoryTree` / `levelView` / `renderPanelHtml` / `pickRowOutcome` 不碰 DOM（可直接在 node 里测），`initCategoryPicker` / `setCategoryPaths` / `syncCategoryPicker` 负责接线。面板一次只显示当前一层：有子级的行带 ›，点了就进这一级并立即按它筛选；顶部有「全部分类」+「← 返回上级」+ 当前路径面包屑；点掉面板/Esc 关闭；`aria-expanded`/`role=listbox` 同步。
  - `web/index.html`：`<select id="filter-category">` 换成隐藏 `<input id="filter-category">` + 按钮 + 面板，所以 `library.js` 里读 `.value`、监听 `change`、清空筛选的代码无需改动。
  - `library.js`：`fillCategorySelect` 改为调 `setCategoryPaths`；`clearFilters` 里补 `syncCategoryPicker("")`；初始化时 `initCategoryPicker()`。
  - `web/css/style.css`：面板/行/箭头/计数/面包屑样式（面板 max-height 340 可滚动）。
  - 后端 `routers/papers.py`：`category` 筛选从 `category = ?` 改成「等于它 或 `LIKE '它 / %'`（带 ESCAPE）」，即**选中一级会连带所有子级**——否则点大类一篇都筛不出来；`%` `_` 按字面处理，不当通配符。
- 测试：新增 `tests/category_picker.test.mjs` 8 条（根层只有第一级、父级篇数按子树合计、点进去才出下一级、三级可继续钻、点击结果展开/选中/收起、根层无返回键而子层有面包屑、名字转义、页面接线与语法）；新增 `tests/test_category_filter_prefix.py` 5 条（父级含全部后代、二级含孙级、叶子只匹配自身、别的分支不混入、`%`/`_` 不当通配符）。`tests/titlebar_ui.test.mjs` 的 web 资源计数 32 → 33。
- **真机端到端（CDP + 真实库副本）**：隔离数据目录复制真实 `paperwhale.db`，开 `--remote-debugging-port`，点开面板读 DOM：第一级 12 行（超分辨与计算成像 60 / 人工智能 24 / 光子计算与神经形态 23 …，无一行含「 / 」）；点「超分辨与计算成像」后出现 4 个子级（44/13/2/1）且列表变成 60 篇（子树合计生效），面包屑与返回键正常。截图 `category-picker-level1.png` / `category-picker-level2.png`（工作区）。脚本 `pw-debug/e2e_category_picker.py`。
- 测试基线：Python **248 项 OK**（新增 5），Node **117 项全过**（新增 8）。
- 产物：`PaperWhale-2.7.14-Setup.exe` 57,739,314 / sha256 `bb559dac5d2ac062b7e00aebf24ce80586178676071de4cae2dee4a05063c914`；便携 `50,597,595` / `ec09087a005f513fa7cbe6fd51aa14f5645df882c5f1300c43213f573a110cb4`；Connector `39,822` / `eb04b2cd61dfaf9c5531eb047419bb7016daf6ed981f0b0e3a13aa6ce4d0a870`。`verify_release --version 2.7.14` 全 PASS（33 个 web 资源）。

### 2026-10-29 2.7.13 发布（修无边框窗口缩放不跟鼠标 / 越拖越宽）

- 用户报障：程序框左右拉宽完全不跟鼠标，会变得非常宽。
- 根因（`web/js/titlebar.js` 的 `beginResize`）：每帧把 `base.w/h/x/y` 更新成上一帧算出的结果，但鼠标位移 `dx/dy` 是「相对拖动起点」的累计值——同一段位移被反复叠加，窗口飞速变宽。上下拖同理，只是用户先发现左右。
- 修复：
  - 拖动期间 `start`（起点几何）只读，每帧用 `起点 + 累计位移` 重算目标；新增 `applied` 只用来跳过「没变化」的桥接调用，绝不回写起点。
  - `setPointerCapture` / `releasePointerCapture`：指针滑出窗口也不丢 pointermove。
  - `pointercancel` 也走收尾，避免监听器泄漏；松手时补发最后一帧。
  - 西/北边改成「先 move 再 resize」（pywebview 的 resize 固定左上角），边缘不跳。
- 测试：新增 `tests/window_resize.test.mjs` 7 条——用 `vm` 跑真实 titlebar.js，伪造 DOM/桥接，派发 pointerdown/pointermove 断言数值：东边 +100 → 1500（不是叠加）、累计 +200 → 1600、南边、西边 move 后 resize 且右边不动、角手柄两轴、最小宽度夹取、松手后不再变化、源码里起点只读 + 指针捕获 + pointercancel。变异验证：把 `start.w` 换成 `applied.w`（复现原 bug）→ 2 条立刻红。
- **真机端到端验证（关键证据）**：用隔离数据目录 + 临时 WebView2 profile 启动 dist 版，开 `--remote-debugging-port`，通过 CDP 向 `.tb-resize-e` 派发真实 PointerEvent，再用 `window.pywebview.api.window_geometry()` 读真实窗口宽度：
  - 新版（2.7.13）：起点 1444 → 鼠标 +100 ⇒ 1544（Δ100）→ 累计 +250 ⇒ 1694（Δ250）✅ 1:1 跟手。
  - 旧版（已安装的 2.7.12，同法测量）：+100 ⇒ Δ100，累计 +250 ⇒ **Δ350**（第一段位移被多算一次），连续拖动时每帧叠加就是用户看到的「越拖越宽」。
  - 脚本留在工作区 `pw-debug/e2e_resize.py`（换成旧版 exe 即可复现 FAIL）。
- 顺带修测试稳定性：`tests/test_category_bulk_reclass.py` 用 `TemporaryDirectory(ignore_cleanup_errors=True)` 并在 tearDown 前 sleep 0.2s——分类队列的后台线程可能在清理目录时还在写 test.db，之前会偶发 `PermissionError: 另一个程序正在使用此文件`。
- 测试基线：Python **243 项 OK**，Node **109 项全过**（新增 7）。
- 产物：`PaperWhale-2.7.13-Setup.exe` 57,735,314 / sha256 `7a0cbb68f5f4c8bc65e3903336c0d65f7ea8c612f79b4a1858ac3a975716a6b7`；便携 `50,593,405` / `67e8208f9107bab49bf37704f8405a27feea26d15c8c145a2e184176d73b45a0`；Connector `39,821` / `2d3ce671b4d3533341780a99a342287dc9adb6c4557dd124ecb8089a08d432f6`。`verify_release --version 2.7.13` 全 PASS。

### 2026-10-29 2.7.12 发布（安装器自动关闭正在运行的 PaperWhale）

- 用户需求：安装程序要能自动识别已经在运行的程序，把它关掉后继续安装。
- 原状：`installer/setup.py` 的 `install()` 里只有一行静默 `taskkill /F /IM PaperWhale.exe`——不告知用户、强制结束不给保存机会；且如果程序以更高权限运行，taskkill 失败后仍去解压，用户最终只看到「没有权限写入」，不知道该退程序。
- 实现（全部在 `installer/setup.py`）：
  - `list_running_instances()`：PowerShell `Get-CimInstance Win32_Process` 按 `Name='PaperWhale.exe'` 取 `ProcessId|ExecutablePath`，跳过安装器自身 PID 与坏行。
  - `request_window_close(pid)`：`EnumWindows` + `GetWindowThreadProcessId` 找到该 PID 的所有窗口，`PostMessageW(WM_CLOSE)`，等 1.2s 看是否自己退出（先礼）。
  - `terminate_process(pid)`：`taskkill /PID <pid> /T /F`（只杀目标 PID，绝不按镜像名批量杀），等最多 6s（后兵）；`_pid_alive` 用 `OpenProcess(QUERY_LIMITED_INFORMATION)+GetExitCodeProcess` 判断仍存活。
  - `close_running_app()`：返回 `(是否全部关掉, 关闭记录)`；成功后 sleep 0.3s 让文件句柄释放；`describe_closed()` 生成给用户看的一句话。
  - `install()` 改为「先 `close_running_app()`，失败抛 `AppRunningError`，再解压」；`main()` 捕获后给明确出路「手动退出托盘程序 / 右键以管理员身份运行」，不再落到「没有权限写入」；安装确认框提前说明会自动关闭并中断正在跑的任务；完成提示里加「已自动关闭正在运行的 PaperWhale.exe，装完已重新启动」。
  - 卸载脚本里原有的按镜像名 `taskkill` 保留（卸载场景合理）。
- 测试：新增 `tests/test_installer_shutdown.py` 9 条（无进程时不动、优先 WM_CLOSE 不强杀、关不掉才强杀、关不掉返回失败、进程列表跳过自身 PID 与坏行、install 里关程序在解压之前、install 不再按镜像名盲杀、无法关闭时抛 `AppRunningError`、提示文案给出恢复路径）。
- 真机验证（不动用户程序）：
  - 只读识别到用户正在跑的 `D:\论文鲸\PaperWhale.exe`（PID 46668）。
  - 起了一个**替身进程**（把 `PING.EXE` 复制成 `PaperWhale.exe` 跑起来），`list_running_instances()` 同时列出替身与用户程序；`request_window_close` 对无窗口的控制台进程无效（返回 False），`terminate_process` 按 PID 强杀成功，且**用户程序 PID 前后一致、未受影响**。结论：识别与「只杀目标 PID」正确，不会误伤。
  - 未做「静默安装覆盖用户当前安装」的端到端测试（会真的关掉用户正在用的程序并改写安装目录；留给用户自己执行升级时验证）。
  - **真机端到端（用户同意后执行）**：`PaperWhale-2.7.12-Setup.exe --silent` 升级 `D:\论文鲸`，安装器日志实测顺序为 `16:28:01 detected existing install (version 2.7.10)` → `16:28:02 running instances: 46668` → `16:28:03 terminated pid 46668` → `16:28:05 installed 2.7.12`。即：自动识别到正在运行的 2.7.10，WM_CLOSE 只把窗口收进托盘（应用不退出），于是走强制结束兜底，安装继续成功。升级后 `/api/health` 返回 `version=2.7.12`，论文库 187 篇 / 已分类 183 / 有全文 141，数据完好。
  - 教训：用 PowerShell `Start-Process -Wait` 等安装器会连它启动的 App 子进程一起等（看起来像卡死），取消该等待还会把 App 一起带走；验证安装应改用「起进程 → 轮询安装器 PID 退出」。
- 测试基线：Python **243 项 OK**（新增 9），Node **102 项全过**。
- 产物：`PaperWhale-2.7.12-Setup.exe` 57,734,545 / sha256 `e097578a52ba732513995d5ab7b28beaa0f6a36dd72273bf25a85458cef9b134`；便携 `50,592,505` / `9c90fff19848fd6bda69e67983f387f7028517ecc253aed1a24d05eb9a0e82b6`；Connector `39,821` / `0a0b351d16d0fce63f68f1b7b6c74ce5871fb5c2d65709897e7be77da3e70479`。`verify_release --version 2.7.12` 全 PASS。

### 2026-10-29 2.7.11 发布（分类助手全量重分类 + 分类进度接进任务中心）

- 用户需求：① 分类助手要有「对所有论文进行重新分类」；② 关掉分类助手页面后后台还在整理吗，能不能把进度放进任务中心。
- 事实核查（读代码确认）：分类跑在后端 `category_assistant._CategoryQueue` 的 daemon 线程里，跟弹窗无关；关弹窗只是看不到进度。旧实现三个坑：前端 `pollStatus` 最多 120 次×2s＝4 分钟就停止显示；分类批次完全没写 `task_batches`，任务中心看不到；程序重启后没有 resume，剩下的停在半路。
- 实现：
  - `category_assistant.apply(guide, scope='unclassified'|'all', move_files=True)`；`select_paper_ids(scope)` / `preview(scope)` 抽出来便于测试；`scope=all` 选全部论文，默认仍是只选未分类。
  - `_CategoryQueue.start(paper_ids, move=...)` 用 `batches.create('category', fresh)` 建批次，worker 里 `batches.finish(..., 'running'/'succeeded'/'failed', error)`；`status()` 改返回 `batches.status(batches.current('category'))`（无批次时回退内存计数）；新增 `resume()`，`main.py` lifespan 里调用，重启后把没做完的分类批次接着排队（先 `interrupt_unfinished('category')`）。
  - `categories.assign_path(..., move=True)` 新增 `move` 参数：False 时只改分类字段、不动磁盘 PDF；`_move_pdf` 仍按原逻辑移动。
  - `models.CategoryApplyIn` 增加 `scope` / `move_files`；`routers/categories.py` 的 apply 透传，新增 `GET /api/categories/assistant/preview?scope=` 给确认框取篇数。
  - `routers/tasks.py` 重试分支支持 `kind='category'`（任务中心「仅重试本批失败项」）。
  - 前端：`index.html` 分类助手弹窗加「对所有论文重新分类…」按钮 + 「PDF 跟着移到新分类目录」勾选；`category-assistant.js` 的 `apply(scope)` 全量前 `window.confirm` 写明覆盖与篇数、payload 带 `guide/scope/move_files`，`pollStatus` 去掉 120 次上限并 `updateTaskBatch('category', …)`，新增 `resumeStatus()` 在重开弹窗时接上在跑的批次；`app.js` 新增全局 `pollCategories()`（每 4 秒，喂任务中心 + 右下角新增「正在批量分类」一行）；`task-center.js` 标签映射加 `category → 分类整理`。
- 测试：新增 `tests/test_category_bulk_reclass.py` 6 条（scope 默认/全量、批次创建与完成后 `task_batches`/`batches.recent()` 可见、失败项带原因、`move=False` 不动文件、`move=True` 移动到分类目录）；新增 `tests/category_ui.test.mjs` 5 条静态回归（按钮/开关存在、payload 带 scope 与 move_files、全量前 confirm、进度写任务中心、去掉 120 次上限、app.js/task-center 认识 category、`node --check`）。**注意**：队列跑在后台线程，测试必须把 `patch.object(llm, 'chat_json')` 包住「入队 + 等批次完成」整段，否则线程会拿到真模型（这是本轮踩过的坑）。全量 Python **234 项 OK**，Node **102 项全过**。
- 产物：`PaperWhale-2.7.11-Setup.exe` 57,729,026 / sha256 `5accb698ecfdd948eccf28f4cf9981c641dfc2eef055fb8b9a74d6525ab9c3db`；便携 `50,592,966` / `e9697944f48c86fcd8695a8609d3dc0deb7e212b50aa6c5ec52e5283caa45fed`；Connector `39,821` / `c984de29428d60c42239d99261d46da8bb4ef74d46093009ce730692d100fa3b`。`verify_release --version 2.7.11` 全 PASS；`D:\论文鲸\browser-extension` 与 repo `extension/` 哈希一致（manifest 2.7.11）。
- 用户侧现状（15:57 装完 2.7.10 后跑的分类）：187 篇里 183 篇已分类，4 篇失败，原因都是 `[WinError 10054] 远程主机强迫关闭了一个现有的连接`（LLM 连接被重置，瞬时网络问题）；这 4 篇仍算未分类，2.7.11 里再点一次「整理未分类论文」或任务中心「仅重试本批失败项」即可。

### 2026-10-29 2.7.10 发布（Zotero 分类节点误入库 + 「必须先定位到真 PDF 才建条目」护栏）

- 用户报障：库里出现「散射成像 / 叠层成像 / 偏振成像 / 衍射光学 / 超分辨 / 单像素成像 / 物理学报 / 激光与光电子学进展」这类条目，全是「未知作者 / 未获取 PDF」空壳。用户明确要求：**必须明确定位到 PDF 文件才允许导入**。
- 真实库取证（只读复制 `%APPDATA%\PaperWhale\paperwhale.db`）：8 条空壳 `source='zotero'`、创建于 2026-10-29T11:32:47（同一秒）、元数据全空。6 条正是用户的 Zotero 分类名。
- 根因（合成 RDF 复现，2.7.9 代码仍可复现）：`importers.parse_zotero_rdf` 只排除 attachment，把 `<z:Collection>`（只有 `dc:title`＝分类名、没有 `z:itemType`）当成文献条目导入。期刊名空壳同源（导出里 `dc:title` 就是期刊名的记录）。
- 修复：
  - `parse_zotero_rdf`：无 `z:itemType` 的结构节点（Collection/Search/Tag/…）一律跳过；新增 `_RDF_NON_BIBLIOGRAPHIC_TYPES` 黑名单。
  - `_save_item(..., require_pdf=True)`：Zotero 文件导入与文件夹导入都开启——没解析到真实 PDF 就不建条目，只 `skipped += 1` 并记入 `skipped_no_pdf` / `skipped_no_pdf_examples`（不再混进 errors）。Obsidian 笔记导入保持 `require_pdf=False`。
  - 真 PDF 校验 `looks_like_pdf`（`importers`）/ `is_real_pdf`（`ingest`）：必须 ≥512 字节且开头是 `%PDF-`；`_scan_pdfs`、`POST /api/import/pdf-folder`（返回 `skipped_not_pdf`）、`ingest.import_pdf_file` 三处都校验，后缀改成 `.pdf` 的网页/残片不会入库。
  - `db.normalize_doi_key`：去 `https://doi.org/`、`doi:` 前缀、小写、去 `.full/.pdf/.html` 后缀；`find_duplicate` 的 DOI 精确查询与标题冲突判断都改用归一化值（真实库里有 `10.1117/1.JBO.23.12.121609.full` vs `…121609`、`10.1364/optica.424980` vs `10.1364/OPTICA.424980` 这种重复）。
  - 前端 `web/js/import.js`：Zotero 导入结果多一行「其中 N 条没有定位到 PDF 文件，按规矩不入库」+ 示例标题；PDF 文件夹导入显示「扫描 N 个有效 PDF」「跳过 N 个假 PDF」。
- 测试：新增 `tests/test_zotero_import_guard.py` 8 条（分类节点不入库、无 PDF 跳过、假 PDF 跳过、`import_pdf_file` 拒绝假 PDF、真 PDF 仍导入、DOI `.full` 与大小写变体判重、不同 DOI 不合并）。全量 Python **228 项 OK**，Node **97 项全过**；`web/js/import.js` `node --check` 通过。
- 产物：`PaperWhale-2.7.10-Setup.exe` 57,725,086 / sha256 `333f453ec04bf25e70f1fb48960abbb25c18a7f97de1cafea744109ac65d51ea`；便携 `50,587,248` / `4f4913688f11c936f3e6ac02feed888ec2668f510d14c64fac0829d7905f5d5e`；Connector `39,821` / `933d873d6c8ded706d9a3fbf44444abcf34fa58e0fe40384c92f655edfdfd32c`。`verify_release --version 2.7.10` 全 PASS；`D:\论文鲸\browser-extension` 与 repo `extension/` 15 个文件哈希一致（manifest 2.7.10）。
- 遗留：用户库里那 8 条空壳尚未删除（等用户确认后清理；清理脚本 `tests/_cleanup_dryrun.py` 只读 dry-run）。另有少部分文件导入条目标题质量差（如「深圳大学硕士学位论文」「Email: …」），PDF 文本抽标题的启发式可再优化。

### 2026-10-29 2.7.9 发布（文件夹 PDF 批量导入 + 按内容配对）

- 用户需求：一次性丢一个文件夹的 PDF，软件按 PDF 内容配对已有论文；配不上就用这个 PDF 新建一条专属论文。
- 实现：
  - `services/ingest.py` 新增 `import_pdf_file(pdf_path, source='folder', category_path=None)`：先 `metadata.extract_pdf_metadata` 抽标题/DOI（没有标题回退文件名）→ `db.find_duplicate` 查已有论文 → 调 `create_paper_from_pdf(meta=merged)` 走原有建库/补 PDF 逻辑 → 返回 `{'id','action','title'}`，`action ∈ created / matched / already_has_pdf`；已有论文若有全文则绝不覆盖，且选定的归档分类会补给没有分类的已有论文。
  - `models.py` 新增 `PdfFolderIn(path, recursive=True, category_path=[])`；`routers/import_.py` 新增 `POST /api/import/pdf-folder`：`_validate_folder` 校验路径、`rglob/glob` 扫 PDF（上限 500 个）、逐文件容错，返回 `{folder, scanned, created, matched, already_has_pdf, errors}`。
  - 前端：`web/index.html` 导入页新增「导入 PDF 文件夹」卡片（路径输入 + 选择… + 扫描并导入 + 结果框）；`web/js/import.js` 新增 `pickPdfFolder()/importPdfFolder()`（桌面端走 `window.pywebview.api.pick_folder`，浏览器模式提示粘贴路径），结果分栏显示扫描/新建/匹配补全/跳过/失败；`desktop.py` 暴露 `pick_folder()`（pywebview FOLDER_DIALOG）。
- 测试：新增 `tests/test_pdf_folder_import.py` 4 条（匹配已有并补 PDF、无匹配新建、已有全文跳过不覆盖、选定分类落到对应目录），mock 掉 Crossref/arXiv 补全；全量 Python **220 项 OK**。
- 产物：`PaperWhale-2.7.9-Setup.exe` 57,721,735 / sha256 `ec2df3f5fb18374f090a1b71f01fc2e1bbcf1f0e5c83879aa06b5a6845409e00`；便携 `50,583,038` / `b3d1ad41ebf8b2c85c2aab314cc365d20ce3c924d2d9f07a7f7c2cd3163cff47`；Connector `39,821` / `8c31919dd0bed08860627fc1c2e2f7e64e4277a6674d5c61e53843c163860918`。`verify_release --version 2.7.9` 全 PASS（13 项，含 32 个 web 资源、54 个后端模块、SHA256SUMS 逐文件核对）。日志工作区 `build-2.7.9.log`。
- 注意：批量导入时每个文件都会走一次 PDF 文本抽取，带 DOI 的还会查 Crossref 补元数据，几百个 PDF 会跑一阵；扫描上限 500 个，超出需分批。

### 2026-10-28 ScienceDirect 实际失败诊断（2.5.2）

- 本批只读检查真实库目标失败记录、已安装 PYZ 和 Edge 未打包扩展目录 manifest，确认当次主程序及 Connector 均为2.5.1。最初只按 cached manifest 筛扩展误判缺插件，用户澄清后通过 path manifest 确认已安装，不能再让用户无依据重装。
- 目标 DOI 10.1016/j.optlaseng.2020.106055，PII S0143816619313120。真实公开HTTP实测 Elsevier linkinghub200的 Refresh 没有.pdf后缀而漏跟；已修复并将刷新按HTTP导航计深度，16请求/时限/循环保护保留。现能到ScienceDirect实际页面，但研究环境仍403；没有声称取得真实全文。
- 用户明确使用浏览器Connector保存，旧版本吞掉所有fetch错误，只看到后台补抓记录。插件新增可选结构化 fetchPdfInPage诊断（默认字符串兼容），网页/扩展环境失败脱敏后最多24项发送pdf_attempts；服务端验证用browser-validation。WebImportIn接受并清洗，pdf_fetch_jobs.report.browser_attempts通过SQLite原子json_set合并，后台_save不能覆盖。没有DB新列。
- library.js 展示浏览器证据与后台补抓；popup转义标题、来源诊断、pdf_warning/identity_warning。invalid base64和身份不匹配不再静默。既有Cookie回退保留，不在诊断记录凭据。
- 最终回归：Python186 / Node47项通过，Edge原会话流程含新增记录展示通过。审查后增加实际PDF文件可用状态，与后台任务历史分开显示，覆盖后台旧失败→插件已有文件→文件失效的回归。真实Edge电脑操作上一轮被URL识别策略阻断，不采用其他自动化绕过。本批未写用户库/插件或运行安装器，只读定位；当前真实论文下载仍待新版浏览器证据验证。
- 计划 docs/superpowers/plans/2026-10-28-pdf-real-failure.md。2.5.2 安装版/便携版/Connector已生成，离线验证全通过：54后端模块、31前端资源、全部技能、编译后的Refresh及浏览器诊断、ZIP/内嵌载荷和SHA256均匹配。日志build/package-2.5.2.log，安装包59,753,013字节，便携版51,588,298字节。未自动安装。下一步需要用户重载2.5.2 Connector后同页面重试，再只读获取目标browser_attempts；不要再次把后台403或合成成功当作插件真实失败根因或真实下载成功。

### 2026-10-28 出版社 PDF 补强（2.5.1）

- Connector 提取改为 async `extractPageMetadata({expandPdfMenu,expectedUrl})`；仅用户保存时短暂展开安全 PDF 菜单，最多 1.5 秒，保留原展开状态。识别同 PII 的 canonical/public_url 代理页，保留代理来源、签名参数、真实 page URL / UA；正文优先，手动地址优先，最多四候选共享 90 秒，保存前后检查标签页归属。
- 后端 Links 同样支持同 PII canonical 代理页、JSON 参数、PDF 中转与正文/补充材料排序。修正原 anchor hint 尾部空格导致纯 .pdf 链接被漏识别的问题。
- `pdf_domains.py` RequestPolicy 默认接实际下载器自建 client；测试注入 client 默认不共享策略，可显式传 request_policy。1 秒同主机启动间隔，429/带 Retry-After 的 503 共享冷却；三次403对凭据 HMAC 上下文暂停60秒，最大512host/16context，进程内有界状态。无 Cookie 明文或摘要进入诊断。
- `pdf_trace.request_referer` 用于请求，和诊断 safe_url 分开：同站保留查询、跨站仅来源域、禁止HTTPS降级及用户信息/换行。webimport 增加受限 pdf_user_agent，保留浏览器 UA（不能等同真实浏览器指纹）。
- 本轮最终集成 Python 174、Node 42 项通过；审查修复下载 JSON queryParams 异常结构中断候选和 supporting-only 标签漏排后。旧会话 Edge 流程首次出现 PDF.js Transport destroyed，立即独立复跑全部通过，未修改阅读器。publisher_extension 的动态菜单/代理/登录态/多候选均使用隔离合成页面，没有触碰真实浏览器配置或用户库。真实用户样例仍未在有权会话内验证。
- 计划 `docs/superpowers/plans/2026-10-28-publisher-acquisition.md`；用户说明 PDF获取说明.md / 版本更新.md 已更新。2.5.1 安装版/便携版/Connector 已生成；离线发布校验全通过，54 后端模块、PDF 域名协调/来源策略编译接线、31 前端资源、所有技能与 SHA256 均匹配。安装包 59,745,422 字节，便携版 51,580,901 字节，Connector 25,641 字节；日志 build/package-2.5.1.log。未自动安装，浏览器需重载新版 Connector。

### 2026-10-28 Zotero / ScienceDirect PDF 对照（源码更新，未重新打包）

- 用户样例 PII S0143816619313120 在研究环境访问返回 403，未声称真实全文已获取。对照 Zotero 官方 ScienceDirect translator、Connector itemSaver、桌面 attachments/file；详细证据及后续方向见 `docs/zotero-pdf-comparison-2026-10-28.md`。
- `pdf_download.py` 和 `extension/popup.js` 补页面内下载 JSON、pdfft 备用入口、按钮和中转页解析；插件优先当前标签页上下文，再回退扩展环境。独立编写兼容实现，没有复制打包 Zotero 运行时。
- 新回归 `test_publisher_pdf.py`、`publisher_extension.test.mjs`；完整隔离回归 Python 151 / Node 37 项通过。真实样例成功仍需有权浏览器会话中验证。2.5.0 发布包保持不变，本批代码未重新打包；后续打包需升级版本。

### 2026-10-28 发现论文精简与检索增强（2.5.0）

- 删除界面中 run.answer 和助手历史里的重复文献清单，保留候选处理列表、折叠的用户请求记录与简要检索信息。候选 title_zh 为独立展示字段；新搜索同轮翻译，旧候选手动补译。`discovery_translation.py` 每次最多 50 篇、每批 10 篇，只在原题名指纹和话题会话 ID 一致时保存译名，不覆盖状态或来源元数据。
- DELETE `/api/discover/sessions/{id}` 归档到 recovery_items 的 discovery_topic，可立即撤销或设置回收站恢复。话题/轮次/候选/对话一起恢复，已入库论文始终保留；恢复生成新的 conversation ID，拒绝删除前迟到结果。删除与明确入库共用 `_actions`；snapshot 在写事务内复查父记录，防止删除后惰性创建孤儿会话。
- `renderProgress` 总高度 240px，共享 captureProgress/restoreProgress 在发现、单篇/知识库问答、画像及分类界面保留滚动与折叠状态。底部跟随，新内容不打断向上阅读。
- 新内置原创 `skills/academic-discovery/SKILL.md` 通过 task_prompt('discovery') 实际进入规划和筛选。增加 Europe PMC core 公开元数据，最多 8 次初始请求；timeline 对真实 OpenAlex 种子最多追加 2 次前后向引用查询，和初始来源共享 38 秒预算。citation_graph 仅接受 canonical OpenAlex ID 和两种来源证据，最终候选仍限 30；不能声称系统性穷尽或阅读全文。
- 发布校验增加全部 skills 文件逐字节匹配，避免仅打包提示词而漏接工具代码。实施计划 `docs/superpowers/plans/2026-10-28-discovery-refinement.md`，用户说明 `docs/发现论文说明.md`。
- 集成隔离验证：Python 146 项、Node 36 项通过；发现论文、共享 AI 反馈和会话浏览器流程通过。审查发现的中英混合题名漏显示译名已修复并经浏览器回归。Europe PMC 与 OpenAlex 前向引用公开请求各返回 12 条带链接记录；未使用用户模型密钥、邮箱或真实库。
- 2.5.0 安装包、便携版、Connector 已生成；`tools/verify_release.py --version 2.5.0` 全通过：31 个前端资源、全部技能文件逐字节匹配、53 个后端模块、实际编译的 Europe PMC/引用工具与技能接线、安装器内嵌载荷、ZIP/版本和 SHA256。日志 `build/package-2.5.0.log`；安装包 59,727,547 字节，便携版 51,562,532 字节。未自动安装。

### 2026-10-28 发现论文双模块

- 页面分别显示谷歌学术邮件推送和 AI 研究检索。新增 `discovery_sources.py`（Crossref/OpenAlex/arXiv）、`research_discovery.py`（有界计划/来源编号筛选）、`discovery_store.py`（话题、轮次和用户候选状态），继续使用原 discover 路由。`discovery:<session_id>` 是新的独立对话作用域，先验只有当前话题历史节选和已保存画像，不混入其他对话。
- 数据库增量增加 discovery_sessions/runs/candidates 三表；搜索成功后检查会话版本再保存候选，不自动导入。用户加入时才入库和补 PDF；忽略和已有状态保留。`strict_identifiers=True` 仅对新候选入库启用，避免同名不同 DOI 误绑定旧论文。
- `llm.chat(progress_channel='structured')` 隐藏模型 JSON 内容增量、保留真实 reasoning；不能把 JSON 作为给用户的回答。来源返回元数据为唯一候选池，模型只能选择编号并给理由/阶段；普通搜索保留来源相关性，时间线覆盖返回日期跨度后排列，latest 默认近一年。所有日期按上海日期，Windows 缺 tzdata 时回退 UTC+08。
- 备份包含新表；兼容完全没有新三表的旧 v1 备份，缺部分新表时拒绝。恢复前仍使用 activity 在途保护。注意新表的父子导入/删除顺序及 discovery 会话消息。
- 关键回归覆盖候选状态与晚到搜索响应竞态、跨话题旧结果、同名不同标识、预算、来源失败和旧备份。后端 115 项、Node 36 项和五份 Edge 流程已通过；公开关键词接口验证未使用用户 LLM 密钥或邮箱。说明见 `docs/发现论文说明.md`，计划见 `docs/superpowers/plans/2026-10-28-ai-discovery.md`。
- 2.4.0 安装包、便携版和 Connector 已生成。离线发布验证通过：31 个前端资源、全部 52 个后端模块、版本、内嵌载荷/ZIP 完整性及全部 SHA256 匹配。包中含 6 份白名单使用文档；未自动安装或改动真实邮箱/文献库。

### 2026-10-27 补强与 2.3.0 打包

- 2.3.2 按用户截图移除密度、标题行数、显示列/调整列宽菜单，固定两行标题和两行高度，始终显示完整列；不再读取旧 `pw-title-lines` / `pw-list-density` / `pw-visible-cols`。表头自由拖动和 `pw.compactCols` 宽度保存继续保留。工作区 Edge 用旧单行、舒适密度和隐藏列偏好注入后回归通过，同时验证宽度保存、两行截断及 233 篇全库流程。
- 2.3.2 安装包、便携版与 Connector 已生成；`tools/verify_release.py --version 2.3.2` 通过所有资源/模块、ZIP、版本和 SHA256 校验。未自动运行安装器，用户文献库未改动。

- 2.3.1 已打包：默认两行标题、两行文字高度的行密度、第二行末尾省略；去掉手动列宽中的 `minmax(...,1fr)` 回撑，五列均可自由拖动、保存宽度，超过窗口横向滚动。默认自动填满与手动像素宽度分开，恢复默认列回到自动布局。表头与行内容列对齐，状态徽标不额外撑高行。
- 工作区 Edge 新回归实际验证默认两行、统一行高、中文/连续英文长标题首行换行及两行截断、拖窄到 100px、放宽到 1300px 后缩窗口仍保留、刷新保存、横向滚动与恢复默认；原 233 篇浏览及工作区流程通过。`tools/verify_release.py --version 2.3.1` 全部通过，更新包为 `release/PaperWhale-2.3.1-Setup.exe` 和对应便携版/Connector。没有自动安装。

- 已按用户要求生成 `release/PaperWhale-2.3.0-Setup.exe`、`PaperWhale-2.3.0-win-x64-portable.zip`、`PaperWhale-2.3.0-Connector.zip`，保留旧 2.2.0 文件；未运行安装器或覆盖当前安装。下面历史实施记录中的“尚未打包”现已由本次发布完成。
- 离线验证 `tools/verify_release.py --version 2.3.0` 通过：两份 ZIP CRC、安装器内嵌载荷与版本、30 个前端资源与源码一致、全部 49 个后端模块及编译版本、Connector 文件一致、SHA256SUMS 全部匹配。此处是产物完整性校验，不代表在用户真实库上执行了升级测试。
- `tools/package_payload.py` 按 `tools/release-docs.json` 白名单加入 5 份使用文档，不随包加入内部核对/工作文档；便携标记仅写入便携 ZIP。打包日志在 `build/package-2.3.0.log`，已安装程序和用户数据均未改动。

- 阅读窗口第一批增强已接入：PDF 原生链接与页内目标、60 条返回/前进历史、批注颜色/备注/关键词/章节筛选与 Markdown 导出；缺目录时本机识别（前 120 页）、可停止和手动调整；本篇 AI 选文/页/节/批注材料预览与准备。独立模块为 `reader-navigation.js`、`reader-annotations.js` / `reader-annotation-panel.js`、`reader-outline.js` / `reader-outline-panel.js`、`reader-context.js`，`reader.js` 负责接线。说明见 `docs/阅读窗口增强说明.md`，计划与验收在 `docs/superpowers/plans/2026-10-27-reader-first-batch.md`。
- `ChatIn.display_question` 只控制用户气泡显示，保存在 `ai_messages.metadata`；`content` 仍保留完整的实际问题和引用材料供模型历史使用。不能用显示摘要替换模型证据，也不要渲染未转义 HTML。AI 默认联网与深度不变。新文件切换依赖 doc + openToken 的归属检查；目录手动调整缓存绑定 PDF 指纹，重新识别会清除旧调整。
- `ChatIn.retrieval_question` 保存实际用户问题给联网与本地检索，`rag.chat` 和 `ai_context.followup_query` 需优先用它，避免准备材料前缀占据搜索/分词预算。PDF 链接层还检查缩放值和原始 canvas，防止旧 `getAnnotations` 返回后覆盖新缩放区域。本批最终验证：Python 73 项、Node 36 项通过；新增阅读器 Edge 8 组、原阅读器 11 组、原会话 8 组及 AI 反馈流程通过。

- 已按 UI 审查补齐分页/全库排序、真实文件与独立处理状态、批次任务中心、回收站及元数据备份、阅读进度与布局恢复、逐命中搜索、异步结果归属、分组保存和键盘支持。AI 保持默认联网与深度分析，显示模型实际分析/公开摘要和回答增量，支持停止/重试、证据类型、指定论文比较和原子追加笔记。
- 恢复备份采用规范临时库校验和事务导入；失败回滚，恢复前自动备份，生成/后台任务在途时禁止恢复，成功后页面重载。不要改为直接覆盖用户 SQLite 后再迁移。新增 `services/activity.py` 协调任务入口，`services/batches.py` / `recovery.py` 及 `/api/tasks/batches`、`/api/maintenance`；数据库增量加独立错误与恢复/批次表。说明与验收见 `docs/界面与功能补强说明.md`、`docs/ui-hardening-plan.md`。
- 本轮最终隔离验证：Python 71 项、Node 8 项、四份 Edge 交互脚本通过；冒烟报告 20/20（1 项沿用脚本原有跳过）。真实数据目录和已安装程序未改动，尚未打包发布。

- PDF 获取新增共享 `services/pdf_download.py`、结构化 `pdf_trace.py`、`pdf_fetch_jobs` 持久化队列与 `/api/pdf-fetch/report/{paper_id}`；支持 OpenAlex 托管 PDF（设置中默认关闭）、OA 落地页/相对链接、流式 80MB 上限、解析/身份校验和有限重试。设置页可选浏览器配置，Cookie 按配置隔离并逐跳匹配，不再猜测关闭新出现的浏览器窗口。来源/版本/哈希保存在 `papers.pdf_acquisition`；详细说明与验证边界见 `docs/PDF获取说明.md`。

- AI 对话改为后端 SQLite 持久化：`paper:<id>` / `library` / `profile` / `categories` 四种独立作用域；论文按 ID 分开保存历史与附件，翻译/解释进入本篇连续对话。
- `services/conversations.py` 负责存储、版本、并发与草稿确认；`services/ai_context.py` 统一任务边界、先验和历史预算；`routers/conversations.py` 提供 `/api/ai/conversations/{scope}` 读取/清空、`materials` 和 `apply` 接口。
- `web/js/conversation.js` 统一客户端会话状态，阅读器切换、迟到响应、附件上传和清空均绑定原始作用域；画像 localStorage 只迁移一次。
- 分类助手加载研究画像、现有分类与真实数量；画像/分类草稿只有明确保存后共享，新讨论或底稿变化阻止旧草稿覆盖。精读、相关性、标签、技能与订阅翻译有独立任务规则。
- 数据库新增 `ai_conversations`、`ai_messages`，旧 `chat_messages` 保留兼容。测试用独立临时目录与 mock LLM；新增后端回归、Node 会话测试和 Edge 实际交互脚本。
- 使用说明：`docs/AI对话规则.md`；设计与实现计划：`docs/ai-conversation-design.md`、`docs/ai-conversation-plan.md`。下文描述 2.2.0 基线，涉及聊天历史与上下文的旧描述以本补充和当前代码为准。

---

## 1. 这是什么

PaperWhale 是一个**本地优先的 AI 文献管理工作台**，Windows 桌面应用（不是 Web 服务）：

- 导入 / 管理 PDF 论文，自动抽元数据、建全文索引
- AI 精读、标签、多级分类、研究画像相关性分析
- 内置 PDF 阅读器：标注、目录、全文搜索、AI 对话
- 从 arXiv / DOI / 开放获取源自动补 PDF；支持浏览器登录态抓出版社 PDF
- 谷歌学术邮件订阅同步
- 浏览器插件“一键存到 PaperWhale”
- 本地 FastAPI + SQLite，LLM 通过用户自配的 OpenAI 兼容 API

用户数据全部在本机，不上云；对话/文档内容会发给用户配置的 LLM。

---

## 2. 快速开始

### 2.1 路径

```text
项目根目录：D:\Harness APP\PaperWhale
Python venv：D:\Harness APP\PaperWhale\.venv\Scripts\python.exe
用户数据：C:\Users\<user>\AppData\Roaming\PaperWhale
数据库：%APPDATA%\PaperWhale\paperwhale.db
日志：%APPDATA%\PaperWhale\paperwhale.log
用户设置：%APPDATA%\PaperWhale\settings.json   ← 含 API Key / 邮箱授权码，禁止提交/外发
默认/当前 PDF 根目录：D:\论文鲸\论文文件
已安装程序目录：D:\论文鲸（PaperWhale.exe）
发布产物：D:\Harness APP\PaperWhale\release\
```

### 2.2 开发运行

```powershell
cd "D:\Harness APP\PaperWhale"
.\.venv\Scripts\python.exe -m uvicorn paperwhale_server.main:app --host 127.0.0.1 --port 8765
```

或桌面模式：

```powershell
.\.venv\Scripts\python.exe desktop.py
```

或浏览器模式：

```powershell
.\.venv\Scripts\python.exe run.py
```

测试/隔离数据时必须设置：

```powershell
$env:PAPERWHALE_DATA = "D:\Harness APP\PaperWhale\tests\.tmp-data"
$env:PAPERWHALE_LLM_MOCK = "1"
```

**不要**在测试时把 `PAPERWHALE_DATA` 指到真实 `%APPDATA%\PaperWhale`，除非明确要修真实数据。历史上出过数据目录被误移的事故。

### 2.3 打包

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1 -Version 2.2.1
```

产物在 `release/`：

- `PaperWhale-<ver>-Setup.exe`
- `PaperWhale-<ver>-win-x64-portable.zip`
- `PaperWhale-<ver>-Connector.zip`
- `SHA256SUMS.txt`
- `使用说明.txt`

版本号需要同步修改这些位置：

```text
paperwhale_server/main.py     APP_VERSION
web/index.html                页面底部/标题版本
installer/setup.py            DEFAULT_VERSION
build.ps1                     param(...) 默认值 和 usage 注释
extension/manifest.json       version
README.md                     发布文件名
```

---

## 3. 技术栈与结构

```text
desktop.py                     pywebview 桌面窗口 + pystray 托盘 + 内嵌 uvicorn
run.py                         纯浏览器模式启动
paperwhale_server/
  main.py                      FastAPI 入口、lifespan、路由挂载、版本
  config.py                    路径 / 设置 / 分类目录 / PDF 目录
  db.py                        SQLite 连接、建表、轻量迁移、通用查询
  models.py                    Pydantic 请求模型
  llm.py                       OpenAI 兼容 LLM 客户端（含 JSON 容错、深度思考）
  routers/                     papers/ai/analyze/annotations/categories/cite/
                               discover/import_/pdf_fetch/settings/scholar/
                               skills/webimport/plugins
  services/                    ingest/metadata/rag/discover/categories/
                               category_assistant/pdf_fetch/pdftext/
                               pdf_migration/importers/tagging/scholar/
                               websearch/webfetch/browser_cookies/skills/
                               citation/analysis_queue
web/
  index.html                   单页应用骨架
  css/style.css                全部样式
  js/                          api.js app.js library.js reader.js settings.js
                               chat.js discover.js import.js categories.js
                               profile-chat.js category-assistant.js scholar.js
  vendor/pdfjs/                PDF.js（不要随意升级）
extension/                     MV3 浏览器插件
installer/                     自解压安装器（Python 生成 exe）
skills/                        随包内置的科研技能（prompt 规范）
word/                          Word 插件源码，当前**不随包发布**
assets/                        图标
docs/                          使用说明、截图、清单
tests/                         冒烟/端到端/安装器测试
release/                       发布产物
```

前端是**原生 ES Modules + 原生 DOM**，没有 React/Vue，没有构建步骤。修改 `web/` 后重启服务刷新即可。

---

## 4. 数据模型（当前）

SQLite，核心表：

```sql
papers(
  id TEXT PRIMARY KEY,             -- uuid4 hex
  title, authors, year, venue, doi, arxiv_id, url, abstract,
  file_path,                       -- 实际存绝对路径优先；兼容相对 data/ 的旧路径
  source, tags, category, summary, key_points, relevance, notes,
  status,                          -- new|indexed|analyzed|failed
  category_major, category_minor,
  category_path,                   -- JSON 数组，任意层级，如 ["光学工程","偏振成像","超分辨"]
  category_suggest,                -- JSON：{"path":[...],"major":...,"minor":...}
  category_status,                 -- approved|pending
  pdf_error,
  created_at, updated_at
)
chunks(id, paper_id, page, text) + chunks_fts（FTS5，触发器同步）
annotations(id, paper_id, page, color, rects JSON, quote, note, kind, created_at)
settings(key, value)
scholar_items(id, message_uid, title, authors, venue, snippet, url, published,
              status, paper_id, title_zh, snippet_zh, created_at, updated_at)
chat_messages(...)
```

分类文件结构（支持 1–6 级）：

```text
<PDF目录>/论文文件/<一级>/<二级>/<三级>/.../<paper_id>.pdf
```

`config.paper_pdf_dir(*segments)` 负责创建目录。

---

## 5. 关键功能与代码入口

### 5.1 PDF 导入 / 索引

- `POST /api/papers/upload`（multipart）→ `services/ingest.create_paper_from_pdf`
- PDF 文本抽取：`services/pdftext.py`（pypdfium2）
- 分页 chunk 写入 `chunks`，触发器同步 `chunks_fts`
- 元数据补全：`services/metadata.py`
  - `extract_pdf_metadata`
  - `ensure_metadata`：精读后缺作者/期刊/年份/DOI 时，按 DOI 或标题查 OpenAlex/Crossref 补齐
  - OpenAlex 优先，按标题相似度 + 引用数选择，避免选到同名论文
- 元数据修复：lifespan 后台 `metadata.repair_bad_titles`

### 5.2 AI 精读 / 标签 / 分类建议

- `POST /api/ai/summarize/{paper_id}` → `ingest.analyze_paper`
- LLM 输出 JSON：summary/key_points/venue/year/authors/category_path
- 分类建议：`categories.register_suggestion(path=...)`；新路径挂起 `pending`，用户确认后 `approve_suggestion`
- 标签：`services/tagging.py`，优先复用全库已有标签
- 研究画像相关性：`ingest.generate_relevance`

### 5.3 多级分类（2.0 起）

- `services/categories.py`
  - `clean_levels`：接受 list 或 `"一级 / 二级"` 字符串，sanitize，最多 6 级
  - `levels_of(paper)`：优先 `category_path`，回退 major/minor/category
  - `assign_path`：写 major/minor/category_path/category，并移动 PDF
  - `taxonomy_paths`：路径级统计；`taxonomy` 仅旧两级兼容
- `services/category_assistant.py`
  - 对话确认分类深度/偏好；批量给未分类论文选路径
  - 队列 `_CategoryQueue`，分类失败目前会写 `pdf_error`（**待改：应写独立错误字段**）
- 前端：`web/js/categories.js` 支持现有路径选择、1–4 级新建；`library.js` 分类筛选用 `paths`

### 5.4 PDF 自动获取

`services/pdf_fetch.py` 策略顺序：

1. arXiv id
2. 无 DOI 时按标题查元数据 + 开放 PDF
3. DOI：Crossref/OpenAlex/Unpaywall/Europe PMC/S2/DOI landing/arXiv 标题
4. 浏览器登录态：
   - `services/browser_cookies.py` 读 Edge/Chrome Cookie
   - 普通 v10/v11 用 DPAPI + AES-GCM 自己解
   - v20 App-Bound 用 `rookie-cookies`（会拉起临时 Edge 进程，可能弹窗；读取后尽量自动关闭）
   - 用 Cookie + Referer 请求出版社页面/PDF
5. 失败写 `pdf_error`

注意：这是**灰区能力**，不要承诺绕过付费墙；只复用用户本机已有登录态。

### 5.5 阅读器

- `web/js/reader.js` + `web/index.html` 的 reader 区域
- PDF.js 连续滚动；按页渲染/回收；缩放、页码、目录（`getOutline`）、全文搜索（逐页 getTextContent）
- 选区工具条：4 色高亮、下划线、删除线、笔记、翻译、解释、追问
- 标注存 `annotations`；`POST/PATCH/DELETE /api/.../annotations`
- 翻译/解释结果进右侧「论文对话」侧栏，不再用浮层
- 论文对话默认 `deep_thinking=true`、`search_web=true`，支持上传 PDF/DOCX/TXT/MD
- 侧栏 tabs：AI 精读 / 论文对话 / 与我相关 / 目录 / 批注

### 5.6 AI 对话 / RAG

- `POST /api/ai/chat`
  - body: `question`, `paper_id?`, `deep_thinking`, `attachments`, `search_web`
  - 有 paper_id：全文喂给模型；否则 FTS/LIKE 检索全库
  - 附件与联网搜索结果拼进上下文
  - 深度思考：配了 `reasoning_model` 走真实 reasoning；否则两段式“内部思考→回答”
  - 返回 `answer/sources/reasoning/model/web_sources`
- `services/rag.py`：检索、上下文构建、引用编号
- `services/llm.py`：JSON 容错解析、`chat(deep=..., with_meta=...)`
- 画像助手：`POST /api/ai/profile-chat`（对话、附件、网址读取、深度思考、画像保存）
- 分类助手：`POST /api/categories/assistant/chat|apply|status`
- 内置技能自动注入：精读用 `nature-paper-card`，问答用 `nature-reader`，分类管理用 `knowledge-ops`

### 5.7 谷歌学术邮件订阅

- `services/scholar.py`：IMAP 读取 Google Scholar Alerts
- 支持 QQ/Gmail/163 授权码；IMAP ID 兼容；解析标题/作者/期刊/摘要/链接
- 去重：标题归一化 + URL；忽略项不重复出现
- 已处理（saved/ignored）不在发现页显示；未处理保留
- 自动翻译标题/摘要（`title_zh/snippet_zh`），用 LLM 批量翻
- 自动同步线程 + 翻译线程；30 分钟缓存

### 5.8 浏览器插件 / Word 插件

- `extension/` MV3：读取页面元数据，页面上下文抓 PDF，失败时带 Cookie 交后端重试
- 权限包含 `cookies`；改权限后必须在 Edge/Chrome 扩展页重新加载
- `word/` VBA 插件：**当前不随包发布**，不要声称可用；安装器已移除 Word 勾选

### 5.9 桌面壳

- `desktop.py`：pywebview 窗口、托盘、隐藏到托盘、退出菜单
- 关闭窗口 = 隐藏，不退出；退出走托盘或设置页 `/api/app/quit`
- 窗口暴露 `resize_window`，详情侧栏打开时向右扩窗
- PyInstaller onedir + onefile 安装器

---

## 6. 测试

```powershell
# 启动隔离 mock 服务
$env:PAPERWHALE_DATA = "D:\Harness APP\PaperWhale\tests\.tmp"
$env:PAPERWHALE_LLM_MOCK = "1"
.\.venv\Scripts\python.exe -m uvicorn paperwhale_server.main:app --host 127.0.0.1 --port 8797

# 后端冒烟
.\.venv\Scripts\python.exe tests\smoke.py

# 浏览器端到端（需要本地 Edge/Chrome + CDP 脚本）
.\.venv\Scripts\python.exe tests\cdp_check.py ...
```

`tests/` 里有安装器、插件提取、CDP 截图等临时脚本；其中 `cdp_check.py`/`cdp_interact.py` 依赖本机浏览器与调试端口。测试脚本不是稳定测试套件，改代码时优先保证接口冒烟与关键流程。

---

## 7. 已知问题 / 技术债（优化优先看这里）

1. **`ARCHITECTURE.md` 已过时**：仍写两级分类和旧 API 签名。以本交接 + 代码为准。
2. **部分文件有中文乱码**：历史 PowerShell 读写导致若干 docstring/注释是 mojibake。功能正常，但可读性差；**不要一次性批量重写**，先小步替换并跑测试。
3. **根目录有个 0 字节文件 `None`**，是误生成，可直接删。
4. **分类助手失败写 `pdf_error`**：`category_assistant._worker` 分类失败会覆盖论文 PDF 错误字段，应改成独立 `category_error` 或只记日志。
5. **分类 UI 仍偏基础**：`taxonomy()` 保留两级兼容；部分旧页面/API 可能只读 `items`，新页面读 `paths`。改名/迁移时注意兼容。
6. **阅读器搜索只跳页，不做命中高亮**；目录只读 PDF 内置 outline；无缩略图、双页、夜间模式。
7. **标注备注用 `window.prompt`**，交互粗糙，建议改成内联编辑弹窗。
8. **所有 LLM 调用非流式**：深度思考可能两倍耗时，HTTP 会长时间挂起；建议加 SSE/分块或后台任务。
9. **本地 API 无鉴权/CSRF**：只绑定 `127.0.0.1`，但同机其他程序可访问；如果改为局域网访问必须先加 token。
10. **敏感信息明文**：`settings.json` 存 API Key、邮箱授权码；不要提交、不要打包进 release。
11. **Cookie 抓取依赖本机浏览器**：Edge/Chrome 版本、App-Bound v20、浏览器锁库、企业安全软件都可能影响；`rookie-cookies` 可能拉起临时浏览器，自动关窗是 best effort。不要把它宣传成“破解付费墙”。
12. **Google Scholar 邮件解析是 HTML 正则**：邮件模板变体会漏/误判；解析失败应降级而不是丢数据。
13. **IMAP 同步**没有 OAuth，使用授权码；QQ/Gmail 需要用户开启 IMAP。同步只取最近邮件（默认 40 封），历史未处理项在 DB 里保留。
14. **Word 插件不可用**：源码在 `word/`，但未发布；不要重新加回安装器除非解决 Office 兼容/编译错误。
15. **扩展权限变更后必须重载**，否则 Cookie 接口不可用。
16. **打包体积**因 `rookie_cookies`、`cryptography`、`ddgs`、`python-docx` 等增大到约 55MB 安装包；优化依赖前先确认功能不退化。
17. **版本号分散**在多处，打包前务必同步（见 2.3）。
18. **真实数据操作要极其小心**：历史上出现过空 `file_path` 被解析成数据目录、整个 `%APPDATA%\PaperWhale` 被移走的事故；`config.resolve_pdf_path("")` 已改为返回不存在的占位路径，移动文件前必须 `is_file()`。

---

## 8. 这次交接前刚完成的内容（2.0 → 2.2）

- 分类支持任意层级（`category_path`），旧数据自动迁移，文件按多级目录归档
- 分类助手可对话确认层级深度，批量给未分类论文选路径
- 阅读器：
  - 4 色高亮、下划线、删除线、笔记
  - 批注备注编辑、删除、点击跳页
  - PDF 目录、全文搜索
  - 翻译/解释进右侧论文对话侧栏
  - 论文对话默认深度思考 + 联网搜索，支持上传文件
- 谷歌学术订阅条目自动中文翻译，已处理条目不显示，未处理保留
- 优化 PDF 元数据补齐（OpenAlex 优先 + 引用数 tie-break）
- Edge v20 Cookie 支持（rookie-cookies）与临时浏览器自动关闭
- 发现页/详情侧栏/紧凑列表等 UI 修复

---

## 9. 建议的优化顺序

1. **先修数据安全与一致性**
   - 分类失败不要写 `pdf_error`
   - 所有文件移动前做 `is_file()`/bounds check
   - 给真实 DB 操作加备份（至少 `paperwhale.db.bak-*`）
2. **补测试**
   - 用 `PAPERWHALE_DATA` 临时目录 + mock LLM
   - smoke 覆盖：上传 PDF、精读、分类 1/2/3 级、批注 CRUD、RAG chat、Scholar 解析、设置保存
3. **API/模型整理**
   - 分类统一 `category_path`，逐步废弃 `taxonomy()` 旧结构
   - 拆 `routers/ai.py` 与 `services/rag.py` 中的大函数
   - 加流式输出（SSE）与后台任务状态
4. **阅读器增强**
   - 命中高亮、缩略图、双页、夜间模式、标注筛选/导出
   - 标注备注改成内联编辑器
5. **依赖与安装**
   - 评估 `rookie-cookies`/`cryptography` 的打包体积与安全软件误报
   - 自动化版本号同步与 SHA256
6. **文档**
   - 重写 `ARCHITECTURE.md`，与当前代码一致
   - 保留本 `HANDOFF.md` 作为接手入口

---

## 10. 给接手者的硬性约束

- 不要删除 / 覆盖 `%APPDATA%\PaperWhale` 和用户的 PDF 目录；要动先备份。
- 不要提交 `settings.json`、`paperwhale.db`、用户 PDF、`release/` 里的安装包到公开仓库。
- 不要用 PowerShell 的 `>`/`Out-File` 直接改写含中文的源码；用 UTF-8（无 BOM）编辑器或 `edit`/`write` 工具，改完做语法检查。
- 任何“批量重整”先在临时数据目录验证。
- 打包前跑一次 `python -m compileall` 或 AST 语法检查；安装后验证 `/api/health`、论文数、分类树。
- 不要声称 Word 插件、付费墙破解、未验证的出版社抓取“已经可用”。

---

## 11. 当前用户数据快照（仅用于理解环境，不要写死到代码）

- 论文：46 篇
- 缺 PDF：11 篇（4 篇 OA 但被反爬拦截，7 篇付费墙）
- 分类路径：20 条（旧两级已迁移到 `category_path`）
- 谷歌学术未处理条目：约 10 条（已自动中文翻译）
- 真实数据路径见第 2 节；交接后如需测试，**不要直接改真实数据**。


### 2026-10-29 深夜 2.7.23：剪贴板监听 + 参考文献外扩 + 方法 / 参数库 + 复习卡片

- 用户需求原话：「盯剪贴板里的 DOI 或者看着像论文题目的；参考文献外扩；方法、参数库；把读过的真正变成自己的。后面两个需要在左边的侧边栏新增栏目」。四条全部实现，两个新栏目进侧边栏（`data-page="methods"` / `data-page="cards"`）。
- 新表（`db.py` SCHEMA）：`method_profiles(paper_id PK, status, method_summary, experiment_type, data JSON, error, created_at, updated_at)`、`flashcards(id PK, paper_id, front, back, page, source, ease, interval_days, reps, lapses, due_at, created_at, reviewed_at)`，索引 `idx_method_profiles_status` / `idx_flashcards_due` / `idx_flashcards_paper`。设置项 `clipboard_watch`、`clipboard_title_guess`（默认都开）。
- 剪贴板监听（我，Chief）：`services/clipboard_watch.py` + `routers/clipboard.py` + `web/js/clipboard.js`。0.9s 轮询 Win32 剪贴板（ctypes），识别 DOI / arXiv（URL / `arXiv:` / 裸 ID）/ 标题启发式；去重（同文本只提示一次、标题已在库跳过、最多 3 条、已处理 id FIFO 200）；`GET /api/clipboard/candidates`、`POST /{id}/accept|dismiss`；入库复用 `ingest.create_paper_from_metadata`（DOI 走 Crossref、arXiv 走 arXiv、标题走 `find_metadata_by_title` 兜底仅标题），无 PDF 自动排 `pdf_fetch`。隐私：只存内存、不写日志、关掉设置连剪贴板都不读；多行段落/长句不弹。
- 参考文献外扩（我）：`services/reference_expand.py` + `routers/references.py` + `web/js/references.js` + 右键菜单项。OpenAlex：DOI 查 `/works/doi:`，无 DOI 用标题搜索 + 归一化标题比对；`references` 读 `referenced_works` 后 `filter=openalex_id:W..|W..` 批量取；`citations` 用 `filter=cites:W..`。已入库条目返回 `existing_paper_id`；`POST /api/references/import` 走 `create_paper_from_metadata`（判重复用），可选排 PDF。
- 方法 / 参数库（接口 Bot 后端，插件 Bot 前端）：`services/methods.py`（取方法章优先的 12000 字，`llm.chat_json` 抽 summary/experiment_type/parameters/materials/equipment/conditions，`method_profiles` 覆盖写，队列 kind=`methods`）+ 4 个接口 + `web/js/methods-page.js`/`css/methods.css`。前端：搜索防抖 350ms、state 过滤、单篇/全部提取、递归 setTimeout 轮询（无 setInterval，切页即停）。
- 复习卡片（接口 Bot 后端，插件 Bot 前端）：`services/flashcards.py`（摘要+要点+正文 12000 字生成 5-10 卡，`page` 夹 1..总页数；先删 `source='ai'` 旧卡再插，手工卡保留；简化 SM-2 固定表）+ 5 个接口 + `web/js/cards-page.js`/`css/cards.css`。前端空格显示答案、1/2/3/4 评分、键盘只绑一次。
- `llm.py` mock 增加两个标记分支：system 含「方法参数抽取器」返回参数 JSON、含「复习卡片生成器」返回卡片 JSON（`PAPERWHALE_LLM_MOCK=1` 下可离线跑全链路）。`routers/tasks.py` 重试接上 `kind=methods` → `services.methods.queue_manager`、`kind=cards` → `services.flashcards.queue_manager`；`task-center.js` 增加两个中文标签；`app.js` 轮询两批并进右下角进度。
- 版本号 7 处已 bump 2.7.23；`extension/` 已同步到 `D:\论文鲸\browser-extension`（15 文件 sha256 全一致，manifest 2.7.23，无功能改动）。
- 测试：**Python 321 项 OK**（基线 268 → 新增 53：剪贴板 17、外扩 11、方法 12、卡片 12 + 之前几轮）；**Node 155 项全过**（新增 features_ui 10、methods_page_ui 7、cards_page_ui 8）。
- 产物：`release\PaperWhale-2.7.23-Setup.exe` 55,356,852 / `a3b7b19a96f2341ea6a893b9a10b78be56943f72d62ac86787e7a110d69585b8`；便携 48,216,223 / `511a49ca709667d651eea8fa9279e869888866e1c1012b03e6cfc05c9c619330`；Connector 43,688 / `87155e798704ab697086b3d2d02246da26c86711d3936531c255c40ce3a111e6`；`verify_release --version 2.7.23` 全 PASS（40 个前端资源、62 个后端模块）。
- 真机证据（本机 2.7.23，PID 39276）：health=2.7.23；`/api/stats` papers=160 / missing_pdf=22 / chunks=4752 未变；CDP 打开两页 **0 JS 错误、0 失败请求**，截图见工作区 `methods-page-2.7.23.png`、`cards-page-2.7.23.png`；真实模型抽取 `741ac7b6…` 成功（数值仿真，12 个参数：400−650nm、3mm 焦距、2.5μm 像素间距…）；真实生成 9 张卡并复习 1 张（interval=1.0、due 次日）；真实 OpenAlex 外扩该篇返回 5 条参考文献。
- 未验证 / 已知问题：**剪贴板真机链路未验证**——本机所有 Python/PowerShell 进程 `OpenClipboard` 都返回 ERROR_ACCESS_DENIED(5)，而同一环境里 Edge 通过 `navigator.clipboard` 可读可写，疑似进程所在窗口站/远程会话限制。应用本身由 harness 拉起，故监听读不到；用户从桌面正常启动时可能正常，需用户复制一次 DOI 验证。若仍不弹，下一步在 `read_clipboard_text()` 里记录一次失败日志（现在静默）并考虑 `GetClipboardSequenceNumber` 轮询替代 OpenClipboard。
- 数据副作用（真实库）：新增 1 条 `method_profiles`、9 张 `flashcards`（可重新生成覆盖）；安装后自动恢复了一个用户早前发起的 `analyze` 批次（60 篇，会消耗 token，属用户自己排的队）。
- 坑（别再踩）：本机 shell 是 **PowerShell 5.1**，`Set-Content -Encoding UTF8` 会写 BOM；`extension/manifest.json` 带 BOM 会让 `tests/pdf_extension.test.mjs` 的 `JSON.parse` 直接报错。批量改文件用 `[System.IO.File]::WriteAllBytes` 或去 BOM。
- 接口契约小偏差（可接受，记录）：显式 `paper_ids` 全部被跳过时统一 400「都已经提取过了」（无 PDF 场景文案不准）；`GET /api/methods` 的 `total` 是完整命中数、`items` 限 200；`profile` 未提取返回 null；`POST /api/cards/{id}/review` 非法 grade 先于卡存在性校验。


### 2026-10-30 凌晨 2.7.24：复习卡片改成「可迁移知识卡」

- 用户报障原话：「复习卡片的内容有点夸张了，你应该提取出一些公共可用的知识，而不是针对这篇论文内容的知识……目的不是让用户多么了解这个论文内容，而是这个论文中能带给用户什么知识，能在之后的科研中用上」；随后补充：「你不能只针对我的研究画像，你需要提出来的问题能够符合当前用户的人物画像才行」。
- 改动（`services/flashcards.py`）：`_SYSTEM_PROMPT` 重写为 8 条硬规则——产物是「可迁移知识卡」；每张卡必须「离开这篇论文也成立」（原理/为什么这样设计/路线取舍/适用边界/失效条件/常见坑）；禁止复述「本文提出/本文实验/本文作者」；不考具体数字、图表、章节、作者名；front 必须是通用问题；back 结论+一句原理（≤80 字），可把本文当例子但答案要普适。
- 画像定位（关键，user 第二条要求）：`settings.profile` 只作为 `user` 消息里的「我的画像（用来决定讲多深、从什么角度切入、用什么术语；不要把我的具体课题写进题目）」；system 里新增第 4 条「不要围绕用户自己的具体课题出题：题目里不出现用户的项目名、样品、装置、实验条件」。难度按画像校准，画像缺失按同领域研究生水平。
- 同步改动：`llm.py` 的卡片 mock 换成通用知识问题（保住「复习卡片生成器」标记）；`web/index.html` 卡片页副标题改成「可迁移知识卡（原理、取舍、适用边界）」；`tests/test_flashcards.py` 新增 `test_generate_asks_for_transferable_knowledge`（钉住「可迁移知识卡 / 离开这篇论文也成立 / 禁止复述 / 本文提出 / 同领域 / 不要围绕用户自己的具体课题出题」，并断言画像只进 user、不进 system）。
- 版本 2.7.24（7 处版本号用 Python 脚本改，避免 PS5.1 BOM 问题）；`docs/版本更新.md` 已补 2.7.24 一节；`extension/` 同步到 `D:\论文鲸\browser-extension`（15 文件 diff=0）。
- 测试：**Python 322 项 OK**（321+1 条新回归）、**Node 155 项全过**。
- 产物：`PaperWhale-2.7.24-Setup.exe` 55,361,654 / `03f20445bbaaa8b41eca894093e8a0cecc7619fe2c479c78560c8cf8a40a6efe`；便携 48,219,126 / `3dffb8af6990874d84d028c870dc8726611375ab6e65868ee66a284035dd4d25`；Connector 43,689 / `682b8cf5a7c6d320b60399a475f2935043211504e66e5b9a8232eb7907c860b5`；`verify_release --version 2.7.24` 全 PASS。
- 真机：静默安装成功（安装器自动关掉 2.7.23 并启动 2.7.24，PID 34588，health=2.7.24）；用真实模型重生成 2 篇（`741ac7b6…` 光学 CNN 前端 + `e4541ff7…` 并行波前单像素成像）共 18 张卡，**0 失败**。抽样质量（人工看过，符合预期）：
  - 「设计自由空间光学卷积系统时，为什么对准误差和阵列配准会成为主要工程瓶颈？」
  - 「混合光电子架构在什么条件下才可能优于纯电子实现？」
  - 「提高单像素/波前成像速度的几条路线——降采样、压缩感知、深度学习、硬件并行——各自代价是什么？」
  - 「并行通道数为什么不能无限增加？」
  这些问题都离开原论文成立、且能迁移到用户自己的课题，没有出现「本文提出/本文实验」。抽样结果文件：`D:\DeepSeek-Harness\data\bot\workspace\pw-debug\cards-2.7.24.md`。
- 注意：**旧卡不会自动变**——要在卡片页点「为全部论文生成」或单篇重新生成才会覆盖（AI 卡覆盖，手工卡保留）；生成全库 160 篇会调 160 次模型，费 token，建议按分类分批。表结构与复习算法未动。
- 已知未做：新卡 `due_at=''` 立即到期（复习页每次取 20 张，不会爆），后续可加「每日新卡上限」或错峰。


### 2026-10-30 上午 2.7.25：Word 引用插件（像 Zotero 那样插入参考文献）

- 用户需求原话：「现在来更新一下，像zotero一样，在 Word 里面的。插入参考文献的插件吧」。
- 起点与三处断点（之前那套没发出去的原因）：`word/` 里早就有 `PaperWhale.bas` / `customUI.xml` / `base.dotm` / `inject_ribbon.py` / `build_word_plugin.ps1`，但 ① 编译出的 dotm **没有注入功能区**、② `build.ps1` 把 `word/` 打包禁掉了（注释 `(word plugin disabled)`）、③ `installer/setup.py` 有 `install_word_plugin()` 却从没被调用。三处都补上了。
- VBA 重写（`word/PaperWhale.bas`，约 900 行）：每条引文 = 书签 `PWC_<n>` + 文档变量 `PW_IDS_<n>`（存 paper_id，支持一条引文多篇）；文末列表 = 书签 `PW_BIBLIOGRAPHY`；编号按书签在文档里的先后顺序，所以 IEEE/Nature/GB/T 的 [1][2] 与列表永远一致。刷新时调一次 `POST /api/cite/document.txt` 拿回「每条引文文字 + 参考文献正文」，再从后往前替换书签文字。
- 功能区（`word/customUI.xml`）：PaperWhale 标签，按钮 插入引文 / 插入·更新参考文献 / 刷新 / 引文格式 / 端口 / 测试连接 / 使用说明。**关键坑**：Word 2021 只认 2006/01 命名空间 + 包根 `_rels/.rels` 里的 2006 版 extensibility 关系（本机 Zotero.dotm 同款）；用 2009/07 命名空间、或把关系挂到 `word/_rels/document.xml.rels`，Word 会静默忽略整个 customUI（表现出来就是「标签页不出现、onLoad 不触发」）。
- `word/inject_ribbon.py`：注入位置/关系类型改成上面那套，并清理旧版留在 document.xml.rels 里的关系。`word/build_word_plugin.ps1`：Word COM 注入 VBA → Start-Process 调 inject_ribbon（PS5.1 坑：Start-Process 的 ArgumentList 不会替你给含空格路径加引号，必须自己包 `"`；脚本保持纯 ASCII，否则 PS5.1 按 ANSI 读会解析失败）。
- 端口与 HTTP：插件先探测 `设置值 → 8765 → 8797`，能通的才用并写回注册表（本机注册表里有旧的 8797，之前正是它导致「无法与服务器建立连接 / 操作超时」）。HTTP 通道三级回退：`MSXML2.ServerXMLHTTP.6.0`（可设超时）→ `MSXML2.XMLHTTP.6.0` → `MSXML2.XMLHTTP` → `WinHttp.WinHttpRequest.5.1`。
- 打包/安装：`build.ps1` 把 `word/PaperWhaleMacros.dotm` 复制进 `dist\word\`；`installer/setup.py` 的 `install()` 调 `install_word_plugin(target)`（Word 检测不到就跳过，失败不挡主程序安装），卸载脚本原本就会删 STARTUP 里的 dotm。`tools/verify_release.py` 放行 `word/` 并校验「包内模板 == 仓库模板」。
- 服务端：新增 `routers/word.py`（GET /api/word/status、POST /api/word/install|uninstall；Word 占用检测、`PAPERWHALE_WORD_SOURCE` 覆盖、frozen 时源路径 = exe 同目录 `word/`）；`routers/cite.py` 新增 `POST /api/cite/document.txt`（纯文本：`id\t引文` 若干行 + `--BIBLIOGRAPHY--` + 参考文献）；`/api/plugins/install-word` 改为委托 `word.install()`。设置页恢复 Word 行（状态 / 安装到 Word / 打开加载项目录）。
- 测试：**Python 348 项 OK**（新增 `tests/test_word_plugin_template.py` 5 条包结构断言 + 接口 Bot 的 word/cite 测试）；**Node 166 项全过**（`tests/word_plugin.test.mjs` 已按最终实现校正：2006 命名空间、MSXML2 首选 + WinHTTP 回退、build 复制模板）。
- 真机验收（装 2.7.25，PID 46552）：`/api/word/status` → word_detected=true / installed=true / source_available=true；STARTUP 里的 `PaperWhaleMacros.dotm` 与仓库 sha256 一致；Word 16.0 功能区出现 **PaperWhale** 标签（窗口截图 `word_window.png`）；E2E（真实库、真实模型无关）：插入 2 条引文 → IEEE 刷新得到 `[1] [2]` + 参考文献 [1][2] → 切 APA 后正文与列表一起重排 → 再点一次「插入/更新参考文献」书签仍是单个 `PW_BIBLIOGRAPHY`（不重复追加）；插件 `PWHealth` 报 2.7.25，说明端口自动探测把旧 8797 纠正成 8765。
- 真机踩到的三个 VBA 坑（已修，勿回退）：① Word 把 `vbCrLf` 折成单个段落标记，书签结尾必须用 `Range.End` 取真实结尾，不能拿 `Len(text)` 算，否则报「数值超出范围」；② 引文后要补一个空格，否则相邻两条引文的书签边界重合，替换后一条会把前一条连同书签一起吃掉；③ 重新挂书签前必须先 `Delete` 同名书签，否则报 5921「书签已存在」。
- 已知限制：一次「插入引文」可以选多条（逗号），但分两次插入的相邻引文不会自动合并成一个括号；书签方案与 Zotero 的 field 不互通（两边不能刷新对方的引文）；Word 里改完格式需要点「刷新」（插件已自动刷新，但手改文档结构后要再点一次）。
- 产物：`PaperWhale-2.7.25-Setup.exe` 55,410,659 / `263eb2d41022a72c69bb5834ac87f0f89702ea5982878a2a2a5a9947801e915a`；便携 48,269,361 / `cb23574930909c50b4fa404cc587d4bec3712104f2748be4a9b69129c0399563`；Connector 43,689 / `8eaa878c83beaba7e847260a285b6b9608873f76f889ab39af31d95d087926ae`；`verify_release --version 2.7.25` 全 PASS（40 前端资源、63 后端模块、Word 模板比对）。


### 2026-10-30 中午 2.7.26：自定义 / 外部导入引文格式

- 用户需求原话：「如何实现，实现从外部导入参考文献格式。或者更智能一点，跟 AI 说我想要什么什么样的参考格式。比如说只需要前面三个作者，然后有多了作者就写 et 点 al 的这样的格式」。
- 设计原则（**以后改这块别偏离**）：AI 只负责把一句中文编译成**结构化格式规格 JSON**，渲染永远由代码确定性完成（不逐篇调模型）；外部 `.csl` 也映射成同一份规格。这样每篇格式一致、可单测、Word 与网页共用。
- 新表（`db.py`）：`citation_styles(id TEXT PK, name, spec JSON, source, created_at, updated_at)`；老库无需迁移（`CREATE TABLE IF NOT EXISTS`）。
- 规格 schema（version 1，字段固定）：`name, numeric, author_limit, inline_author_limit, et_al, inline_et_al, name_format(family-initials|initials-family|family), initials_dots, author_delimiter, last_author_join, inline_wrapper(paren|bracket|none), inline_year_sep, bib_field_sep, bib_year_style(paren|plain), bib_order[ authors,year,title,venue,doi,url ], italic[], title_quotes, doi_style(url|plain|off), url_style(on|off), trailing_period`。缺字段补默认、非法值 `ValueError`→400。
- 后端（接口 Bot）：`services/citation_style.py`（校验/渲染/AI 编译/CSL 导入/CRUD）、`routers/cite.py` 追加 `GET /api/cite/styles`、`GET /api/cite/styles.txt`、`GET /api/cite/styles/{id}`、`POST /api/cite/styles/ai`、`POST /api/cite/styles/import`、`POST /api/cite/styles/{id}/preview`、`DELETE /api/cite/styles/{id}`；`services/citation.py` 加自定义分发 + `is_numeric()`（内置 6 种输出逐字节未变）。`llm.py` mock 加标记「引文格式编译器」（离线可测）。
- 真机 E2E 发现并在交付前修掉的 3 个渲染 bug（回归已补）：① 正文截断多一个逗号 → `(Xiang et al., 2023)`；② 参考文献截断后又带 `&` → 应为 `Xiang, S., Shi, Y., Zhang, Y., et al.`（一旦截断，前 N 位用 delimiter，不用 last_author_join）；③ CSL 的 `citation` layout `prefix="[" suffix="]"` 没映射成 `inline_wrapper="bracket"`。
- 前端（插件 Bot）：`web/js/citation-styles.js` 导出 `initCitationStyles()`，启动后在 `.settings-grid` 末尾自建「引文格式」卡片（因为 `settings.js` 的 `organizeSettings()` 会在初始化时清掉旧卡片，不能写死在 index.html）；AI 描述框 + 生成 + 可选文件名 + `.csl` 文件导入 + warnings + 选中预览 + 删除自定义（confirm）。`app.js` 已接线（我改的）。
- Word 端（我）：`PWSetStyle` 改成从 `GET /api/cite/styles.txt` **动态拉列表**（内置 6 + 自定义/导入的都能选，标「← 当前」），选中的 id 存进文档变量 `PW_STYLE` → 之后刷新/插引文自动用自定义规则；Word 里不需要重启也能看到新格式（每次点「引文格式」都重新拉）。
- 测试：**Python 392 项 OK**（新增 `tests/test_citation_style.py` + `tests/test_citation_style_api.py`，含用户例子「4 位作者→前 3 + et al.」）；**Node 173 项全过**（新增 `tests/citation_styles_ui.test.mjs` 7 条）；`tests/test_word_plugin_template.py` 增加「BAS 必须拉 /api/cite/styles.txt」断言。
- 真机证据（装 2.7.26）：① 用真实模型把用户原话编译成 spec（author_limit=3、et_al、italic=[venue]、doi_style=off），预览正确；② 导入测试 CSL → spec `numeric=true, author_limit=3, inline_wrapper=bracket, title_quotes=true, bib_order=[authors,title,venue,year,doi]`，warnings 仅「DOI 按裸 DOI 渲染」；③ 17 位作者论文 → 正文 `(Xiang et al., 2023)`、条目 `Xiang, S., Shi, Y., Zhang, Y., et al. … *Opto-Electronic Advances*.`；④ Word 真机：自定义样式 `custom-47e9078e`（AI 生成）下正文 `(CNKI, 2024) (Yin et al., 2025)`、参考文献 `Yin, Z., Lu, Y., Hu, B., et al. (2025). Parallel wavefront single-pixel imaging. *Applied Physics Letters*.`，无 DOI、书签 `PW_BIBLIOGRAPHY` 正常。
- 产物：`PaperWhale-2.7.26-Setup.exe` 55,449,464 / `623563a1763f5eb9b944cf74daf7f59ad8ce594a737198ed9399781f62c8a0fb`；便携 48,306,774 / `090a8f8fa468e084a09801fc06d724cc2f1a1e6f59fe06aa38e72fd89b409237`；Connector 43,689 / `d0b9f2ceed92f2f5c12a40a7e754b6f18650cdd3e10dfde450a794e2043be3be`；`verify_release --version 2.7.26` 全 PASS。
- 已知限制（写进产品预期，不做成 bug）：CSL 只做「常见属性映射」，`<group><choose>`、特殊 locale、卷期页等高级结构会被简化并列入 warnings；库里 papers 表没有 volume/pages 字段，所以 CSL 里卷期页部分渲染不出来；自定义样式的预览用样例论文，不代表你库里每篇都完全一致；删除自定义格式后，Word 文档里存着的该 id 会静默退化成 APA 默认渲染（不报错，重新选格式即可）。


### 2026-10-30 中午 2.7.27 / 2.7.28：卷期页 + Nature 排版修正 + 一次文件损坏恢复

- 用户需求原话：「卷期页基本上所有的都会要求啊，不过格式一般都是相同的，你需要确保抓取的论文资料里有这一项」。
- 2.7.27（卷期页全链路）：`papers` 新增 `volume/issue/pages`（`db._migrate` 老库自动补列），`update_paper`/`PaperPatch` 放行；抓取侧 `metadata.CONTRACT_FIELDS` 加三字段，Crossref（volume/issue/page）、OpenAlex（biblio）、网页 highwire/prism meta、EndNote XML、RIS、BibTeX、Zotero RDF/CSV 全部映射，`normalize_volume/normalize_issue/normalize_pages` 统一清洗（页码 en dash）；`ingest._insert_paper/_enrich_existing` 落库且只补空；内置 6 种样式各自排版（`vol. 5, no. 4, pp. 260004` / `2026, 5(4): 260004` / `5(4), 260004` / `*Journal* 5, 260004 (2026)` 等），空值输出与旧版逐字节一致；自定义规格允许 `volume_pages` token（`12(3): 45–67`）且 AI 编译默认插入到 venue 之后；PDF 导入走 DOI 补全时也合并卷期页。新增 `POST /api/papers/backfill-bibliographic` + `/status`（后台线程、每篇 0.2s、只补空、上限 500）与设置页「补齐卷期页」按钮（进度写在按钮下方）。UI：编辑弹窗三个输入框、详情侧栏 `12(3): 45–67`、`style.css` 加 `.grid-3`。
- 真机回填：138 篇有 DOI 且缺卷/页的论文 → **126 篇更新、12 篇失败**；160 篇里现在 90 篇有 pages、82 篇有 volume+pages。
- 2.7.28（Nature 排版修正，真机抽样发现）：作者段不再出现双句点（`Liu, L..`→`Liu, L.`）；Nature 只输出卷号不带期号；MLA/Chicago 的 `et al.` 后多余句点一并清理。基线测试按新输出更新。
- **事故与恢复（重要教训）**：12:04 跑「批量替换版本号」脚本（`.venv\Scripts\python.exe bump_version.py`，逻辑是 read_bytes→decode→replace→write_bytes）时，把脚本涉及的 **7 个文件写成全 0 字节**：`web/index.html`、`paperwhale_server/main.py`、`installer/setup.py`、`build.ps1`、`README.md`、`extension/manifest.json`、`installer/version.txt`（大小不变、内容全 NUL）。git 仓库没有 commit，index 里只有旧版（2.6.x 体量），无法直接恢复；无管理员权限用不了 VSS 影子副本。恢复方式：
  - `web/index.html` 从 `release\PaperWhale-2.7.27-win-x64-portable.zip` 的 `_internal/web/index.html` 提取（字节一致），再改版本号；
  - `extension/manifest.json` 从 `PaperWhale-2.7.27-Connector.zip` 提取后改版本号；`installer/version.txt` 直接重写；
  - `paperwhale_server/main.py` 按会话里读过的全文 + 后续改动重写（lifespan 增加 methods/flashcards resume、clipboard 启动；routers 增加 cards/clipboard/methods/references/word；版本 2.7.28）；
  - `build.ps1` 按此前测试报错时打印过的完整源码重写（含 word 目录打包、连接器 zip、SHA256SUMS）；
  - `installer/setup.py` **重写**（原 719 行无副本）：保留 `list_running_instances/_pid_alive/request_window_close/terminate_process/close_running_app/AppRunningError/install(payload,target,version,launch)` 契约、静默 `--silent [--dir] [--no-launch]`、tkinter 向导、close→extract→write_uninstaller→install_word_plugin→shortcuts→register_uninstall→launch 顺序、卸载脚本；`test_installer_shutdown.py` 9 条全过；
  - `README.md` 重写（覆盖现状功能）。
  - 恢复后验证：Python **429 项 OK**、Node **179 全过**、`verify_release --version 2.7.28` 全 PASS、用新安装器真机装 2.7.28（自动识别并覆盖 `D:\论文鲸`、装 Word 模板、建快捷方式、注册卸载项、启动进程 health=2.7.28）。
  - **教训**：以后不要再写「批量读改写」脚本；改文件用 edit 工具逐处改，或写脚本前先 `Copy-Item` 备份到工作区。损坏文件留档在 `D:\DeepSeek-Harness\data\bot\workspace\pw-corrupt-backup\`。
- 2.7.28 产物：`PaperWhale-2.7.28-Setup.exe` 58,503,856 / `4841b799d91d4ca5e1be4783f45ca9c90b6c3dd7b731fd80c6f6c19b5fc575c9`（重写后的安装器带 tkinter 向导，比旧包大约 3MB）；便携 48,318,797 / `e3446cd81247398dee51b1736332a38096f786be72557ae0222975ae28da0c58`；Connector 43,688 / `f28b60a8a79cb20b9b84490f5b996e6d4624fe15528468413af38c4a50506dc9`。
- 已知限制：backfill 只按 Crossref DOI 查，不再走 Unpaywall/OpenAlex 兜底；12 篇失败的 DOI 可能不存在或 Crossref 没卷期；老库里没有 DOI 的条目只能手动补卷期页。


### 2026-10-30 下午 2.7.29：浏览器保存也带卷期页 + 「场地」Bot 接手新项目

- 2.7.29：`routers/webimport.py` 的 `WebImportIn` 增加 `volume/issue/pages`，并且**没收到这三个字段时从 `body.page_html` 兜底解析**（旧版扩展也生效）；`extension/popup.js` 从页面 meta 抓 `citation_volume/citation_issue/citation_firstpage/citation_lastpage` 与 `prism.*` 变体，first+last 合成 `45–67`，随 `/api/import/web` 提交；`watch`（手动下载接管）payload 自动继承。新增 `tests/test_webimport_bibliographic.py`，扩展侧回归补在 `tests/pdf_extension.test.mjs`。测试：**Python 437 项 OK、Node 183 全过**；产物 Setup 58,505,499 / `274fc67b5603d98871d2e9c1b5f1427f1c7d0a554b6a9972932c5dca1b07a2c8`，便携 48,320,092 / `18e3f2646a13877055f0779cb84ae8b874b582ac2750d211511d5dfd1dffcc0d`，Connector 44,104 / `200279968bebf95761d979f21a562fa2f26e16116fd5888f6db39ae85caaae38`；`verify_release --version 2.7.29` 全 PASS；已安装（health=2.7.29），扩展已同步到 `D:\论文鲸\browser-extension`。
- 安装器补强（源码已改，2.7.30 起生效）：2.7.29 升级时实测出现「先 taskkill 成功、但 PaperWhale.exe 句柄晚一拍释放 → `extractall` PermissionError」→ 现在 `install()` 对 `extractall` 做 6 次 × 0.8s 重试，仍失败才抛 `AppRunningError` 并提示手动退出托盘/管理员运行；`test_installer_shutdown.py` 9 条仍全过。
- 用户新开长期项目：`D:\DeskTop\Python\自动抢场地v5.0`（羽毛球场地自动预约，Python + Selenium + Tkinter）。已建 Bot「场地」（role 抢场）。**注意：系统策略不允许我把它的 work_dir 指到 `D:\DeskTop\...`，需要用户在设置里手动给该 Bot 授权工作目录**。
- 场地项目报障：「抢到场了不发邮件了」。Chief 初步排查（已写进 Bot 的任务书）：三个 `config_*.toml` 的 `[notifications.email]` 正常（163 SMTP、收件人 1049846483@qq.com）；`logs/gui.log` 最后一次发信 **2026-10-29 08:01:03**（`execute_booking_flow` 点完支付 → `notify_booking_success`）；**2026-10-23 的多条 `quick_direct_snatch` / `main._try_book_targets` 成功后面没有发信日志**；代码线索：通知只在 `booking_process._notify_booking_success`（约 3168 行，被 3190 行调用）触发，而 `monitor.handle_available_courts`、`monitor.quick_direct_snatch`「同时指定」分支、`main._try_book_targets` 都走 `booking_process.book_court_simple`（约 3538 行），疑似该简化路径成功后不通知。用户可能运行的是 `dist_fresh/` 打包的旧 exe，源码修完需重新打包才生效——已让 Bot 查清并先征得用户同意。

### 2026-10-30 下午 2.7.30：Word「插入引文」改成 Zotero 式窗口（列全库 / 搜索 / 多选）

- 用户原话：「用户不会记住论文的名字的，你需要把所有库里的文章都列出来，以清晰的方式呈现，也需要能一次加入多个引用。像 zotero 一样！」（此前是 InputBox 让用户自己输关键词、输入序号选）。
- 新文件 `word/PWCitationPicker.code.bas`（UserForm 代码模块）：控件全部在 `UserForm_Initialize` 里用 `Me.Controls.Add` 动态创建，dotm 里只需要一个空窗体，**不用存二进制 .frx**，布局只改这一个文本文件。控件：搜索框 + 搜索/全选/插入引文/取消 按钮 + ListBox（`MultiSelect = 2`、`ColumnCount = 4`、`ColumnWidths = "40;110;292;152"`、4 个表头 Label 年份/作者/标题/期刊）+ 状态/提示 Label。双击插入、回车搜索、`PWSelectionIds` 按行序拼 id、结果写 `gPickerIds`，取消清空。
- `word/PaperWhale.bas`：`PWInsertCitation` 改成 `PWCitationPicker.Show vbModal` + 读 `gPickerIds`（先 `PWRefreshAll(False)`）；新增 `Public gPickerQuery / gPickerIds / gPickerRows`、`Public Function PWGet`、`Public Function PWUrlEncode`（按 UTF-8 百分号编码，跳过 ADODB.Stream 的 3 字节 BOM——这个 BOM bug 真机抓出来过）；自动化入口 `PWTestPickerInsert`（从文档变量 `PW_TEST_ROWS` 选行比如 "1,3,5"，不显示窗口走完 列表→多选→插入 链路）；删除旧 `PWChoosePicks / PWItemId / PWItemLabel / PWSearch / SEARCH_LIMIT`；使用说明文案同步。
- `word/build_word_plugin.ps1`：构建时 `VBComponents.Add(3)` 建空窗体 `PWCitationPicker`（Width 660 / Height 424 / StartUpPosition 1），再把上面的代码 `AddFromString` 进去；同时移除旧的 type 1/3 组件；文件保持 ASCII-only（PS 5.1 编码坑）。
- 后端（接口 Bot）：`routers/word.py` 新增 `GET /api/word/papers.txt?q=&limit=500`，纯文本 TSV：首行 `#count<TAB>total<TAB>returned`，之后每行 `id<TAB>year<TAB>authors<TAB>title<TAB>venue<TAB>has_pdf`；`q` 对 title/authors/venue/doi 大小写不敏感子串匹配；year 降序（空 year 最后）、同 year 按 title 升序；authors 是「第一作者姓 + et al.」短串（≤40 字符）、title ≤200（超出加 …）、venue ≤80；字段里的 tab/换行替换成空格；limit 夹 1..500；查询异常返回 `#count<TAB>0<TAB>0`（Word 弹窗不能吃 500）。新增 `tests/test_word_papers_text.py`。
- 测试：**Python 448 项 OK**、**Node 191 全过**（插件 Bot 在 `tests/word_plugin.test.mjs` 新增 8 条：窗体代码存在 + `MultiSelect = 2` + `/api/word/papers.txt` + 无 InputBox、PWInsertCitation 走 `Show vbModal`、构建脚本 `VBComponents.Add(3)` 等）。
- 真机验证：① 用真实库副本起 dev server(8797) 验接口：`#count 160 160`，中文搜索命中 4 篇；② Word 截图 `pw-debug/citation-picker-2.7.30.png`（880×565）：表头四列、160 行、选中行高亮、状态「共 160 篇」、底部按钮完整；③ 安装 2.7.30 后对 **8765 真实库** E2E：`PW_TEST_ROWS=1,3,5` → `PW_IDS_1` 三个 id，正文 `(Huang et al., 2026); (Feng et al., 2026); (Ding et al., 2026)`，citeCount=1，lastError 空。
- 产物：Setup 58,522,908 / `6972906cb6a5b2d9b15ac44e0ce0ff07fe6210a15048bdd91797badd96784512`；便携 48,336,391 / `2ea0a62026d2743d2cce6fa3f5c3e03defc3f38629715e5f69240044d830c35f`；Connector 44,104 / `1364921b04c2f2e83b00d719b8cbaed5662b09571af6386aa045bf80b6b3a31f`；`verify_release --version 2.7.30` 全 PASS；已静默安装到 `D:\论文鲸`（health=2.7.30，PID 23908（第二次打包：无 PDF 标记改成标题前缀 `[无PDF] `，后缀会被列宽截掉））；扩展 15 文件同步到 `D:\论文鲸\browser-extension`（diff=0，manifest 2.7.30）；STARTUP 里的 `PaperWhaleMacros.dotm` 与仓库一致（安装器装的）。
- 已知限制：列表上限 500 篇（超出时状态栏提示「输入关键词缩小范围」，不做分页）；没有点击表头排序（后端固定 year 降序）；Word 正开着的话需要重启才加载新模板；`PWTestPickerInsert` 自动化入口留在 BAS（与既有 `PWInsertCitationById` 同类，不影响用户）。

### 2026-10-30 下午 2.7.31：Word 引文窗口加分类筛选 + 界面照主程序配色

- 用户原话：「需要能自己选分类栏，然后在里面选文件，因为用户可能把一个文章的参考文献都专门保存在一个单独分类中。然后我需要插件弹窗拥有和主程序类似的ui风格，反正是要好看一点」。追问后确认指的是 **Word 的「插入引文」窗口**（不是浏览器扩展弹窗）。
- 后端（接口 Bot）：`routers/word.py` 的 `GET /api/word/papers.txt` 加 `scope`（空=全部 / `__none__`=未分类 / 路径=该类+子类，与 `q` AND，`total` 反映过滤后总数）；新增 `GET /api/word/categories.txt`（`#stats\t<papers_total>\t<uncategorized>\t<category_rows>`，数据行 `depth\tpath\tcount`，depth=路径段数-1，count=整棵子树篇数，树前序排序，合成的父节点也会输出以免缩进跳级）；测试 `test_word_papers_text.py` 17 条 + 新 `test_word_categories_text.py` 8 条；全量 Python **462 项 OK**。
- `word/PWCitationPicker.code.bas`：新增 `cboCategory`（`Style = 2` 只读下拉，49 项 = 全部 160 篇 / 未分类 0 篇 / 47 个分类按 depth 缩进、括号里是子树篇数）、`PWLoadCategories`（解析 `#stats` 与 `depth/path/count`，接口失败退化成单个「全部」）、`PWCurrentScope`（下拉索引 → scope 的并行数组 `mScopes`，0=`""` 1=`__none__` 其余=路径）、`cboCategory_Change` 用 `mLoadingCats` 防递归、`PWLoadPapers(q, scope)` 请求拼 `&scope=`（`PWUrlEncode`），`PWCategoryLabel` 用 `String$(depth*4)` 缩进。窗口从 660 加宽到 720、列表 696、列宽 `40;110;360;169`。
- 主题（`PWApplyTheme`，照主程序浅色内容区）：`Me.BackColor = RGB(244,246,249)`、列表白底藏青字 `RGB(16,32,58)`、主按钮青绿 `RGB(14,124,134)` 白字加粗、搜索/全选/取消白底藏青字、表头藏青加粗、底部 `lblTip/lblStatus` 灰 `RGB(107,124,143)`、分隔线 `lblDivider`（`RGB(226,232,240)`，`BackStyle=1`）。改完真机截图 `pw-debug/citation-picker-2.7.31.png`（960×565）。
- 真机 E2E（2.7.31 已装，8765 真实库 160 篇）：分类下拉 49 项，item0 `全部（160 篇）`、item1 `未分类（0 篇）`、item2 `人工智能（21 篇）`；选中「人工智能」后列表 21 行、状态「共 21 篇」；`PWTestPickerInsert` 选 1/3/5 → `PW_IDS_1` 三个 id、正文 `(Huang et al., 2026); (Feng et al., 2026); (Ding et al., 2026)`、citeCount=1、lastError 空；颜色断言 `BackColor=16381684`（RGB244,246,249）、`btnInsert=8813582`（RGB14,124,134）。
- 测试：Python **462 项 OK**；Node **200 全过**（插件 Bot 补了 9 条分类/主题回归：`tests/word_plugin.test.mjs` 28 条，含 7 次变异验证）。
- 产物：Setup 58,531,588 / `0162d3131e07bbc5d521717f17a0e265335168ee9c6289f20352fe2d08b0ba9d`；便携 48,344,940 / `61d3675cde0dcbcd2ed46a2ee39eb94312affd4742db91d81703281cca11b9b9`；Connector 44,103 / `1021e93f263c316ea0f6f9591055d623fc89be7573431f612130a21bf48d658c`；`verify_release --version 2.7.31` 全 PASS；已静默安装到 `D:\论文鲸`（health=2.7.31，PID 22996）；扩展 15 文件同步 `D:\论文鲸\browser-extension`（diff=0）；STARTUP 的 dotm 与仓库一致。
- 已知限制：分类下拉是扁平缩进列表（不做逐级展开；主程序里的逐级分类选择器没搬过来）；列表仍上限 500 篇；分类 + 关键词是 AND，没有「多分类多选」；主题只做到配色/字号层级，VBA 控件圆角与阴影做不了（窗口标题栏仍是系统样式）。

### 2026-10-30 下午 2.7.32：Word 引文窗口按 Zotero 版式重排（左分类 / 右条目 / 顶搜索）

- 用户发来 Zotero 的「添加引注」窗口截图并说「你参考一下」→ 把 2.7.31 的「顶部分类下拉 + 单列表格」改成 Zotero 式版式：顶部整条搜索（`lblSearchTag` + `txtQuery` + `btnSearch` + `btnAll`）· 左侧分类列表（`lblCategoryTag`「我的文库」+ `lstCategories`，我的文库/未分类/各分类按 depth 缩进带篇数）· 右侧条目表（`lblHeadTitle/Title/Authors/Year/Venue` 四个表头 + `lstPapers`，`ColumnWidths = "330;110;45;127"`）· 底部 `lblDivider/lblTip/lblStatus/btnInsert/btnClose`。
- 分类控件从 ComboBox 换成 ListBox：事件从 `cboCategory_Change` 改为 `lstCategories_Click`（保留 `mLoadingCats` 防递归），`PWCurrentScope` 读取 `lstCategories.ListIndex` + `mScopes`；分类接口失败降级为 `lstCategories.AddItem "我的文库"`。
- 新增 `PWFitLayout`：按 `Me.InsideHeight` 贴底排 footer（列表高 `ch-140`、分隔线 `ch-70`、提示 `ch-64`、状态 `ch-44`、按钮 `ch-68`），解决 2.7.31 状态栏被窗口底边裁掉的问题（实测 InsideHeight=510.75、状态栏 Top 494+18=512 溢 1.25）。
- 列序改为 Zotero 习惯：标题（`AddItem`，带 `[无PDF] ` 前缀）/ 作者 / 年份 / 期刊。
- 窗口 720×424 → **880×540**（`build_word_plugin.ps1` 的 Width/Height）；窗体标题改「添加引注 · PaperWhale」。
- 新增自动化入口 `PWTestSelectCategory(rowIndex)`（按分类行号选中并重载列表），`PWTestPickerInsert` 支持文档变量 `PW_TEST_CATEGORY`。
- 真机 E2E（2.7.32 已装，8765 真实库）：分类 49 项（我的文库 160 / 未分类 0 / 人工智能 21 …），`PWTestSelectCategory(3)` → 21 行、状态「共 21 篇」；`PW_TEST_CATEGORY=3` + 选 1/2 行 → 插入 `(Huang et al., 2026); (Lv et al., 2025)`，citeCount=1、lastError 空。截图 `pw-debug/citation-picker-2.7.32.png`（1173×720）。
- 产物（第二次打包，含自动化入口）：Setup 58,532,029 / `c682b2e9dae0d5c4fd02b10e222795ecd5b6c380acde9287febbd46ea5aa1239`；便携 48,346,185 / `4c1a4e2d9cae476677563dfcfb26a9bb48259fd498e8cbc04b9ef429171dc6da`；Connector 44,104 / `bb524c08c5a2230c9beb687941ce58673fa604a28902ea339cbfa7adbc78efa8`；`verify_release --version 2.7.32` 全 PASS；已装（health=2.7.32）；扩展 15 文件 diff=0；STARTUP dotm 与仓库一致（60,993 字节）。
- 插件 Bot 已按最终文本更新 `tests/word_plugin.test.mjs`：32 条（改 4 + 扩 1 + 新 4），`node --test tests/*.test.mjs` → **204 / pass 204 / fail 0**（在 `tests/.tmp-mut` 影子树做 7 次变异全红）；它读到的是加 `PWTestSelectCategory` 之前的 60,456 字节快照，最终 dotm 60,993 字节、测试仍全绿。

### 2026-10-30 下午 2.7.33：Word 引文改序号式（[1-3] 折叠）+ 窗口尺寸自适应

- 用户反馈原话：「插入内容需要是按序号顺序标注的『[1-3]』『[1]』『[1,2]』这种形式，或者是其他的标准形式。然后插件页面不能是用滚轮上下滑动选取。右下角的按钮也被遮挡住。论文列表要按照每一条的背景白、浅绿、白、浅绿的交替变换。」另附 Zotero 添加引注窗口截图作参考。
- 后端（接口 Bot）：`POST /api/cite/document.txt` 每行改为三列 `id\t<index>\t行内引文`；`GET /api/cite/styles.txt` 每行改为 `id\tname\t<0|1>`（1=数字型）。tests/test_cite_document.py 扩到 17 条左右。
- `word/PaperWhale.bas`：`PWDocumentReply` 解析三列并编码成 `idx & Chr(1) & inline`（`PWEntryIndex`/`PWEntryInline`）；`PWRefreshAll` 在替换前 `gNumericStyle = PWIsNumericStyle(style)`；`PWBuildCitationText` 数字型分支收集编号 → 新函数 `PWNumericBracket`（升序，连续 ≥3 → `a-b`，否则逗号；1 篇 `[1]`、2 篇 `[1,2]`、3 篇 `[1-3]`、1,2,5 → `[1,2,5]`）；`PWIsNumericStyle` 读 styles.txt 第三列并缓存 `gNumStyleKey/gNumStyleVal`；`PWStyle` 默认 `apa` → **`ieee`**。真机：8765 真实库一次插 3 篇 → 正文 `[1-3]`；切 APA → `(Huang et al., 2026); (Liu et al., 2026); (Feng et al., 2026)`。
- 窗口尺寸踩到的两个 MSForms 坑（**别再重复**）：① 赋值 `ListBox.ColumnWidths` 会把控件宽度重置回设计值；② `ListBox.Clear` 会把控件尺寸重置成设计尺寸（72×72.8），而且 **Show 时还会再恢复一次设计尺寸**——所以布局必须在 `UserForm_Activate` 里再排一遍（`PWFitLayout` + `PWRelayoutLists`，后者用 `.Left/.Top/.Width/.Height` 逐个设，`Move` 在 Clear 之后不可靠）。
- 窗口：保留 2.7.32 的 Zotero 式布局（`lstCategories` + `lstPapers`），新增 `PWFitWindow`（`ActiveWindow.Height - 60` / `Width - 60`，下限 380/720）+ `PWFitLayout` 全部按 `Me.InsideWidth/InsideHeight` 动态排（`listH = (h - 68) - mPaneTop`、底部按钮 `h - 34`、`ColumnWidths` 运行时拼），修掉用户截图里「右下角按钮被遮挡」。
- **未完成（已尝试后回退，务必知道）**：用户要的「白/浅绿交替行背景」和「滚轮不误选中」用 MSForms ListBox 做不到（无行背景 API；滚轮会改 ListIndex）。我按自绘方案（Label 行 + ScrollBar + `PWRowEvents` WithEvents 类）改过一版，但在 UserForm_Initialize 里连续 ReDim/建 ~100 个控件时 VBA 反复报「类型不匹配」/挂 VBE 断点，不可靠，已整体回退到 2.7.32 的 ListBox 实现；`word/PWRowEvents.cls.bas` 与构建脚本里的 class 注入已删除。后续要做得换别的路子（例如窗口改成浏览器 HTML 页面 + 服务端回传选择），不要在 VBA 自绘上继续硬啃。
- 产物：Setup 58,533,951 / `809338b55cfdb38fadc848b33067a58f3230701a79c9d555c57f4e158c84a12f`；便携 48,349,757 / `b878ea5371fbce84677d9ff83896ce9211b49f6ef182473b0ab1fd2785f6fdc1`；Connector 44,103 / `b2976f041589427a1d6c6a5618daa66b38915c6e2ec589366719ea692dad6dac`；`verify_release --version 2.7.33` 全 PASS；已装（health=2.7.33）；扩展 diff=0；STARTUP dotm 63,803 字节与仓库一致。
- 插件 Bot 已更新回归：`tests/word_plugin.test.mjs` **37 条**（改 8 + 新增 5：三列解析 / PWEntryIndex/Inline / PWNumericBracket 四种形态 / PWIsNumericStyle / 默认 ieee），`node --test tests/*.test.mjs` → **209 / pass 209 / fail 0**；在 `tests/.tmp-mut` 影子树做了 10 次变异验证全红（含把 `- 60` 写成 `- 600` 抓住断言过松的问题）。它读到的是加 `UserForm_Activate` 之前的 63,715 字节快照，最终 dotm 63,803 字节、测试仍全绿。
- 注：`PWFitLayout` 最终用 `Me.Width - 6` / `Me.Height - 24`（Show 之前 InsideWidth/InsideHeight 不可靠），与最初任务描述的 Inside* 不同，插件断言按最终文本钉。

### 2026-10-30 下午 2.7.34：修「插过一次就插不了第二次」+ 分类树状显示 + 引文格式可搜索窗口

- 用户反馈：「左侧列表要更好显示层级关系；右边论文列表依旧没有明暗相间；依然无法用滚轮调整列表；引文格式窗口要改成栏目+搜索；插入引文后好像无法再插入新的引文；搜索栏会默认填入 word 中划中的内容，不需要」。
- **根因（重要）**：`PWInsertCitation` 原来把文档里选中的文字预填进搜索框。刚插完一条引文时选区里正是 `[1-3]` 之类，再点「插入引文」→ 搜索词是 `[1-3]` → 列表 0 条 → 看起来像「插不了第二次」。修复：`gPickerQuery = ""`，一律从全库开始。真机验证：连续两次插入 → `citeCount=2`、正文 `[1] [2]`、lastError 空。
- 左侧分类：新增 `PWTreePrefix`，按后端 `depth` 画出 `├─` / `└─` / `│` 树线（`ChrW(&H251C/&H2514/&H2502)`），子分类归属一眼可见。
- 「引文格式」：新 UserForm `word/PWStylePicker.code.bas`（`PWStylePicker`），搜索框 + 列表 + 「使用这个格式/取消」，从 `/api/cite/styles.txt` 读 `id/name/数字型`，当前格式标「← 当前」、数字型标「（数字型）」；`PWSetStyle` 改成 `PWStylePicker.Show vbModal` 后读 `gStylePicked/gStylePickedName`，旧的「输入序号」InputBox 与 `PWStyleIndex` 已废弃。构建脚本新增注入这个窗体（`PWStylePicker` 460×420）。
- **未解决（VBA 控件硬限制，已确认）**：右侧论文列表的「白/浅绿交替行背景」和「鼠标滚轮滚动」用 MSForms ListBox 都做不到——ListBox 无逐行背景 API，且 Word 的 VBA ListBox 不接受滚轮（滚轮会被窗体吞掉）；上一轮自绘方案（Label 行 + ScrollBar + WithEvents 类）在初始化时不稳定，已回退。要真正做到只能像 Zotero 那样把选择窗口做成**独立窗口（HTML/浏览器渲染）**，滚轮与斑马纹都是原生支持。已向用户说明并等其确认这一方向。
- 产物：Setup 58,543,601 / `f053cb810f33d79bf65e6c5cab48d028ca051c086b2ec81ba784404d73d9988d`；便携 48,357,897 / `08eddd687ff8283a280f03f66f87b233182bd4b656bcb997ffdb016fc29822db`；Connector 44,099 / `34ddfaabf538ddf231d041a4837583559ce603dd60d901099e401f134006bf61`；`verify_release --version 2.7.34` 全 PASS；已装（health=2.7.34）；扩展 diff=0；STARTUP dotm 71,138 字节与仓库一致。
- 插件 Bot 已更新回归：`tests/word_plugin.test.mjs` **42 条**（改 2 + 新增 5：不预填检索词 / style 窗体 6 控件与搜索过滤 / PWSetStyle 弹窗链路 / 构建注入 style 窗体），`node --test tests/*.test.mjs` → **214 / pass 214 / fail 0**，影子树 10 项变异全红。
- 遗留死代码（下次动 Word 侧时顺手删）：`PaperWhale.bas` 的 `PWStyleIndex`（无调用）与 `PWSelectionQuery`（`PWInsertCitation` 已不用）；`PWFilterStyles`/`PWSelectedStyleIndex` 重复过滤谓词，建议抽 `PWStyleMatches(i, q)`。

### 2026-10-30 下午 2.7.35：修「在已有引文前面插入新引文」的书签位置（自动重编号）

- 用户提问：「我插入了 [1-6] 之后，如果在他的前面插入了两篇，那它能自动变成 [3-8] 吗」→ 验证时发现真 bug：Word 在书签**起点**插入文字时会把旧书签撑大（起点不动、终点右移），刷新时旧引文起止算错，导致新引文被删/旧引文不重排。
- 修法（`PaperWhale.bas` 的 `PWInsertCitationGroup`）：插入前先记下「范围起点正好等于插入点」的所有 `PWC_*` 书签；插入占位符 + 尾随空格后，若这些书签起点没动（被撑大），就 `bm.Delete` 再用 `Range(startPos + delta, bm.Range.End)` 重新挂同名书签（delta = Len(占位符)+1）。之后照常 `PWRefreshAll` 按文档顺序重排。
- 真机验证（8765 真实库）：先插 6 篇 → 正文 `[1-6]`；光标移到文首再插 2 篇 → 自动变成 `[1,2] [3-8]`（旧引文自动变 [3-8]，新的是 [1,2]），citeCount=2、lastError 空；书签 `PWC_2@0..5 | PWC_1@6..11` 正确。
- 产物：Setup 58,545,420 / `422ceafc1053207c934e301e72903c22750ed6a95328ca148d2a97ab3af13d64`；便携 48,358,381 / `4962a2cd997c66199e84e5e5829e55c274146021335c157b1da6069ef005c0b2`；Connector 44,100 / `edbd6ea97b6f2e310ecbcae72e7b9e9763d377ca210381773f3812d4ecba91b3`；`verify_release --version 2.7.35` 全 PASS；已装（health=2.7.35）；扩展 diff=0；STARTUP dotm 72,251 字节与仓库一致。
- 待办：插件 Bot 可补 `PWInsertCitationGroup` 的「旧书签被撑大要重新挂」断言（`protectNames` / `bm.Range.Start = startPos` / `Range(startPos + delta, bm.Range.End)`），以及 changelog newest 2.7.35。

### 2026-10-30 傍晚 2.7.36：Word 引文选择改成 Zotero 式独立网页窗口（斑马纹 / 滚轮 / 分类树）

- 用户确认「做啊」后按 Zotero 的做法实现：Word 里不再弹 VBA 灰色列表，而是打开本地网页窗口（浏览器渲染），滚轮与斑马纹都是浏览器原生能力。
- 新增页面：`web/word-picker.html`、`web/css/word-picker.css`、`web/js/word-picker.js`。深色顶栏 + 左侧可展开分类树（含子树篇数）+ 顶部搜索 + 右侧四列表格（**白/浅绿 zebra**、`无 PDF` 标签）、Ctrl/Shift 多选、双击直插、Enter 插入、Esc 取消、`beforeunload` 用 `sendBeacon` 发取消。页面复用现有 `/api/word/categories.txt` 与 `/api/word/papers.txt`。
- 后端（接口 Bot）：`main.py` 加 `GET /word-picker` 页面路由；`routers/word.py` 加 `POST /api/word/pick`（token/ids，去重保序、上限 500、TTL 300s、内存字典 + 线程锁）与 `GET /api/word/pick?token=`（一次性返回 `id1,id2` / `__CANCELLED__` / 空串），新测试 `test_word_picker_bridge.py`（11 条，全量 Python **478 项 OK**）。
- Word 端（`PaperWhale.bas`）：`PWInsertCitation` 改成：生成 token → 写文档变量 `PW_PICK_TOKEN` → `ActiveDocument.FollowHyperlink` 打开 `/word-picker?token=...` → `DoEvents` + `Sleep 150ms` 轮询 `GET /api/word/pick`（超时 240s）→ 拿到 ids 后走原来的 `PWInsertCitationGroup` + `PWRefreshAll`；新增 `PWNewToken`（`PW_PICK_TOKEN_OVERRIDE` 文档变量可固定 token 供 E2E）、`PWSleepMs`（`Declare PtrSafe Sub Sleep`）。VBA 旧窗体 `PWCitationPicker` 保留但不再被调用。
- 真机验证：① 页面（Edge）截图 `pw-debug/word-picker-page.png`（1936×1056，分类树 49 项、zebra、footer 共 160 篇）；② 用 `PW_PICK_TOKEN_OVERRIDE=browserE2E` + 真实 `FollowHyperlink` 打开 Edge → 手动 POST 两条 id → Word 正文 `[1,2]`、count=1、lastError 空；③ 安装 2.7.36 后在 8765 真实库复跑同样流程 → `[1,2]`。
- **过程事故**：改 `PWInsertCitation` 的 python 批量替换时误删了 `PWInsertCitationGroup`/`PWNextCitationNumber`，导致编译错误「子过程或函数未定义」（用户截图看到了）。已恢复这两个函数（含 2.7.35 的书签起点保护逻辑），复测编译与插入正常。教训同前：不要用「起止标记整段替换」改 VBA。
- 产物：Setup 58,554,135 / `9ad927b0c43eafd6d5392fbee68bbd6d8255aa6953b3c5f129ec249bbf250597`；便携 48,368,587 / `4f3eee4d270a62b603fad5b1374a6811ce1f2cd6216d1e6fd4436367fa2eee1b`；Connector 44,099 / `c67fa8e42f1ae4bc85c464ca30c8b0ecaa6b16649e985acaab92d838a4183537`；`verify_release --version 2.7.36` 全 PASS；已装（health=2.7.36）；扩展 diff=0；STARTUP dotm 与仓库一致。
- 插件 Bot 已更新回归：`tests/word_plugin.test.mjs` **47 条**（删 2 条旧 VBA 弹窗链路 + 新增 7 条：token 超链接 / 轮询 / PWNewToken+PWSleepMs / 页面外壳 / zebra CSS / JS 数据回传 / JS 交互），全量 Node **219 / pass 219**，影子树 12 项变异全红；dotm 74,197 字节。
- 待办（小）：① `PWNewToken` 是「时间戳+随机数」，同一 Timer 刻度连续调用理论上可能撞 token → 加模块级自增计数保证唯一；② 死代码清理：`PWSelectionQuery` 已无调用、`PWCitationPicker` 窗体（约 18.6KB）随 dotm 发布但不再被插入流程调用——要不要摘掉窗体待定（摘掉会再动一轮插件测试）。

### 2026-10-30 晚 2.7.37：补全《使用说明》+ Word 插件帮助文案

- 用户点桌面程序标题栏「帮助 → 使用说明」后说「补全」：原 `docs/使用说明.txt` 只有 2.5.2 时期几段（还写着「Word 插件开发中，暂未随包发布」），内容严重过时。
- `docs/使用说明.txt` 重写（10,756 字节，14 节）：这是什么 / 安装与升级（含静默、便携、安装器自动关进程）/ 系统要求 / 第一次使用 / 导入文献（PDF、DOI/URL、EndNote/RIS/BibTeX/Zotero、插件、剪贴板、引用外扩）/ 文献库（三级分类、卷期页、补齐、回收站、分类助手）/ AI 功能（精读、问答、阅读器、方法与参数库、复习卡片、任务中心）/ Word 插件（新「添加引注」网页窗口、数字型 [1]/[1,2]/[1-3] 与自动顺延、格式窗口、模板位置、编译错误处理）/ 浏览器插件（重载扩展、卷期页、验证页提示）/ 设置页要点 / 数据与隐私 / 卸载 / 常见问题 / 许可。构建脚本用 `docs/*.txt` 选第一个作为随包说明，仍指向这一份。
- `PaperWhale.bas` 的 `PWShowHelp` 同步重写（2.7.37）：独立选择窗口、滚轮/多选/双击/Enter/Esc、数字型折叠与前面插入顺延、格式窗口可搜索、并指向 docs\使用说明.txt。
- 产物：Setup 58,560,247 / `6e4e84d5114b4abe0c47dd519382375f93676958f77e1d39b80cda6227ce9a43`；便携 48,374,400 / `19e28a3a784655a70cea516c7883f623636996148c14e41843b72f21918c3872`；Connector 44,099 / `aa81c44319d05fa6efd7dfcc0f68d714d80eaf97bc6456a50d4a0844b3bbba6d`；`verify_release --version 2.7.37` 全 PASS；已装（health=2.7.37），随包 `D:\论文鲸\使用说明.txt` 10,756 字节；扩展 diff=0；STARTUP dotm 与仓库一致。

### 2026-10-30 晚 2.7.38：修「帮助 → 使用说明」点不开

- 用户反馈点菜单「使用说明」没反应。根因：`desktop.py` 的 `open_guide()` 只在 `ROOT`（PyInstaller onefile 下 = `_MEIPASS` 临时解包目录）里找 `使用说明.txt`，而安装器/便携版把说明放在 **exe 同目录**；找不到就静默 `return False`，前端 `api()?.open_guide?.()` 也没提示，所以看起来「点了没反应」。
- 修法：① `open_guide()` 候选顺序改为「exe 同目录 → ROOT → exe 目录/docs → ROOT/docs」，打开失败记日志，全部落空也 `log.warning` 列出尝试过的路径；② `web/js/titlebar.js` 新增 `openGuide()`：await 桥接结果，`false` 或异常时 `alert` 提示「没找到《使用说明.txt》，请到安装目录查看或重装」；浏览器模式（无 pywebview 桥）也明确提示；菜单项改调 `openGuide()`。
- 产物：Setup 58,561,409 / `e8dbee0b54edd44e7cb5519d94ca0f12988bfa0469cabe573c9a53289033b9c7`；便携 48,375,687 / `da0dbb5c41a31a24ffe782e82641db94735d32a7ecb76ac871f1869e64fb38ed`；Connector 44,099 / `9473cb115dc69ffbeeb868ae8fcbc541d7b2ba4ad6bf9f0ef94847d6e51312e5`；`verify_release --version 2.7.38` 全 PASS；已装（health=2.7.38，PID 8512）；`D:\论文鲸\使用说明.txt` 存在（10,756 字节）；扩展 diff=0。
- 未做真机「点菜单」验证（pywebview 桥无法从外部触发）；逻辑上 exe 同目录命中。若用户仍反馈点不开，看 `%APPDATA%\PaperWhale\paperwhale.log` 里的 `找不到使用说明文件` / `打开使用说明失败` 日志。

### 2026-10-30 晚 2.7.39：《使用说明》改成应用内排版弹层（不再记事本）

- 用户反馈「用记事本打开太垃圾，肯定要好看点的 UI 和字体」→ 2.7.38 的 `open_guide` 仍走 `os.startfile`（系统 txt 关联）。现在改成应用内阅读面板：
  - 新增 `web/js/guide-content.html`（说明正文的语义化 HTML 片段，含目录/章节/列表/代码块/备注框，v2.7.39）与 `web/css/help.css`（弹层 + 文档排版：深色标题卡、左侧粘性目录、章节左侧青色竖条、代码 chip、`dt/dd` 问答样式、窄屏降级）。
  - `web/js/titlebar.js` 新增 `openGuide()`：动态加载 help.css → 造 `#pw-guide-overlay` 弹层 → `fetch("/js/guide-content.html")` 注入正文；支持点遮罩 / Esc / 「知道了」关闭，右上角「在文件夹中查看」仍调 `pywebview.api.open_guide()` 打开原始 txt；加载失败有兜底提示。菜单「帮助 → 使用说明」改为调它；另外 `#help` 哈希可直接打开（`http://127.0.0.1:8765/#help`），方便截图/自动化。
  - 2.7.38 的 `open_guide` 路径修复保留（作为「在文件夹中查看」的兜底）。
- 真机验证：安装 2.7.39 后 Edge 打开 `http://127.0.0.1:8765/#help` 截图 `pw-debug/help-content.png`（弹层正常：深色标题卡 + 目录 + 正文排版 + 右上角按钮）；`/js/guide-content.html` 与 `/css/help.css` 均 200（7292 / 4502 字节）。
- 产物：Setup 58,567,033 / `20d01c5083db19584461f2c1bd27e0e61a33c533fca83a010b2c3aa0ba041888`；便携 48,381,344 / `9f9c6f082899465919bc292f4432e1507a09d9bb309bfa6fdc6cb85ac2392230`；Connector 44,100 / `864e3da91ec1affbc7b17f0661aac88f69f9da925c78b934426414de6907a80e`；`verify_release --version 2.7.39` 全 PASS；已装（health=2.7.39）；扩展 diff=0。
- 注意：说明内容现在有两份（`docs/使用说明.txt` 随包 txt 与 `web/js/guide-content.html` 应用内版），改说明时两份都要同步；`guide-content.html` 顶部版本号也随版本 bump。

### 2026-10-30 深夜 2.7.40：说明面板排版修复 + 按设计规范重做 + 安装 frontend-design 技能

- 用户截图反馈《使用说明》弹层「目录和内容重叠」，并指出「设计 UI 和程序一致性不够规范」，要求去 GitHub 找程序一致性 / UI 审美相关的 skill 安装进来。
- 排版根因：`guide-content.html` 里各 `<section>` 是 `.pw-guide` 网格的直接子元素，CSS Grid 自动放置把第二节起排到第二列后再回绕到第一列，与左侧 TOC 重叠。修法：把正文包进 `.pw-guide-body`（`grid-column: 2`，`max-width: 74ch`），TOC 固定第一列。
- 设计规范（按新装的 `frontend-design` 技能做的自查）：字号 14px / 行高 1.7 与主程序一致；间距走 8 的倍数（28/32/40）；弹层圆角/阴影复用主程序 `--radius`/`--surface`/`--line` 变量；TOC 176px、当前章节滚动高亮（IntersectionObserver + `.active`）、链接与按钮统一 focus-visible 描边；按钮只留一个主操作（知道了），次操作（在文件夹中查看）白底。
- 技能安装：`frontend-design`（来源 https://github.com/anthropics/skills/tree/main/skills/frontend-design ，Apache-2.0），用 `save_capability` 装成共享 skill 并 attach 到 Chief 与「插件」Bot；以后做 UI 都按它的「设计方向 / 排版 / 避免模板化默认」流程自查。平台本身已有 design-system、design-token、web-design-guidelines、ui-ux-pro-max 等技能。
- 真机验证：安装 2.7.40 后用独立 Edge profile 打开 `http://127.0.0.1:8765/#help` 截图 `pw-debug/help-content.png`（1280×900）：目录与正文分列、无重叠、当前章节高亮、按钮与主程序一致。
- 产物：Setup 58,567,060 / `d26f07c777a40443af1b1722a09d1f1c5e62767730a72859f707fda31dd1d9c9`；便携 48,384,024 / `20416de4dd81f954125ab7f7bf557b439985aaa7b870afac34e0aa0419ca74df`；Connector 44,099 / `4653aac70d29c47e520be4280630749199d3740ba3ae6f44d7c91c75b60f13ec`；`verify_release --version 2.7.40` 全 PASS；已装（health=2.7.40）；扩展 diff=0。

### 2026-10-30 深夜 2.7.41：UI 审查第一批 P0（可读性 / 焦点 / 遮挡 / 引注页配色）

- 起因：用户要求「用 skill 审查整个程序，看看有什么值得美化和优化的地方」，审完说「好的，实行」；先做 P0 七条。完整清单（P0 7 / P1 39 / P2 8）在 `D:\DeepSeek-Harness\data\bot\workspace\PaperWhale-UI审查清单-2.7.40.md`。
- 审查方法：`frontend-design` + `web-design-guidelines`（Vercel 规则原文拉取超时，按规则清单逐条核对）；真实库副本 + Playwright/Edge 截 14 个页面；9 个 CSS 静态扫描（2019 行、227 hex + 62 rgb）。
- P0-1 焦点环：`style.css` 输入框基础规则里的 `outline:none`（0,1,1）压过全局 `:where(...)` 的 `:focus-visible`（0,1,0），所有文本框只剩 13% 透明度阴影。删掉 `outline:none`，现在输入框有 `3px solid #14a3a3`（offset 3px）——computed style + 截图实测。
- P0-2 次级文字：`--ink-3 #7a8ba1` → `#5f7186`（灰底 3.21→4.63、白底 3.48→5.01，达 WCAG AA）。57 处共用（副标题/表头/作者/期刊）。
- P0-3 任务中心遮挡：悬浮按钮实测 `[1338,845,82,35]` 与列表行重叠。改为侧栏底部按钮（`.sidebar-foot` 内、`#llm-pill` 之上，`◷ 任务中心`），面板改 `left:244px; bottom:20px`；实测 `rowHits=0`，`.page` 底部 padding 回到 48px。
- P0-4 斑马纹：`--zebra #f2faf7`（对白 1.06，肉眼不可辨）→ `#e5f2ec`（1.15）；`--select #d7ece6` + 选中行 `inset 3px 0 0 var(--accent-2)` 左侧标记；去掉该处 `!important`。
- P0-5 引注页配色：`word-picker.css` 自建 `:root` 且 accent/accent-2 与主程序相反。改为 `--ink-2 #44566f / --ink-3 #5f7186 / --accent #0e7c86 / --accent-2 #14a3a3 / --radius 14px`，主按钮渐变改 `160deg` 与主程序一致；各用法同步（focus 边、ghost hover、btn hover、active cnt）。
- P0-6 参考文献外扩：`.reference-item` 白底白边 → `var(--line)` + `var(--surface)`；`.rp-flag` 由 `#8fd6ff`（对白约 1.4:1）改 `var(--accent)` + `#cfe9e6` 描边。
- P0-7 `.pc-meta` 重复定义（12px/ink-3 与 13px/ink-2，后者覆盖）：删后一条，统一 `12.5px` + `var(--ink-3)`。
- 回归：Python **478 项 OK**（`python -m unittest discover -s tests -p "test_*.py"`，exit=0；输出里的 `sqlite3.OperationalError: no such table: pdf_fetch_jobs` 是既有测试的预期噪声）；Node **227 / pass 227 / fail 0**（新增 `tests/ui_p0_2_7_41.test.mjs`；`word_plugin.test.mjs` newest 改 2.7.41 + 引注页主副色断言按新值）。
- 产物：Setup 58,569,984 / `7d773ab19266b3dc9419c974a211f065a29bd3f61e5bd02a7b2e76757eb56e26`；便携 48,384,545 / `4ae15972d2a30e1a3512d4c6512a3c6140c40326846a73609e0c4ab3b77ada0c`；Connector 44,099 / `fb77dec260cb00e013ce6c9c0ac0d50ad76f66747e527fd6a06e1eb288830110`；`verify_release --version 2.7.41` 全 PASS；已静默安装到 `D:\论文鲸`（`/api/health`=2.7.41）；扩展同步 `D:\论文鲸\browser-extension` diff=0（15 文件）。
- 真机验证（装好后 8765 真实库）：`--ink-3=#5f7186`、任务按钮父级 `sidebar-foot`、`rowHits=0`、焦点环 `solid 3px rgb(20,163,163) offset 3px`、引注页 `accent=#0e7c86 / accent-2=#14a3a3 / zebra=#e5f2ec`；截图 `pw-debug/final-2.7.41/`。
- 待办（清单第二/三批）：抽 `tokens.css`、字号 23→7 档、圆角 13→6、图标换 SVG、状态列重做、深色模式与 `prefers-reduced-motion`。

### 2026-10-30 深夜 2.7.42：设计令牌收口（tokens.css + 字号/圆角刻度 + 死代码）

- 用户说「做」第二批（设计系统收口）。新增 `web/css/tokens.css` 作为唯一入口（颜色/表面/线条/深色外壳/语义色/阴影/圆角/字号/行高/字体），`index.html` 与 `word-picker.html` 都先引 tokens 再引页面样式；`style.css` 的 `:root` 删除；`word-picker.css` 只留 `--zebra/--select` 两个别名（指向 `--row-alt/--row-selected`）。
- 刻度归位（脚本按映射替换，逐文件计数）：字号 **218 处**、23 档 → 7 档（11/12/13/14/16/18/24；`22/30/40/44` 是图标/emoji 尺寸，作为例外保留）；圆角 **102 处**、13 档 → 6 档（`--radius-sm 4 / chip 6 / control 8 / card 14 / pill 999`，输入框 8px 与 9px 并存的问题随之消失）；字重 4 处（650→700、620→600，非可变字体本来就会吸附）。
- 颜色收口：`help.css` 19 处错误 fallback（`var(--ink-3, #6b7c8f)` 与真值不一致）清掉；`reader-enhancements.css` 25 处硬编码 → `--line/--line-strong/--surface-3/--ink-2/--ink-3`（PDF 搜索高亮与链接叠加的 7 个内容色保留）；`ai-feedback.css` 从一行 2KB 展开成可读格式，并修掉两个根本不存在的变量（`--muted`/`--border`，原来永远走 fallback）；`reader.js` 的 PDF 占位卡/浮动层内联 `cssText` 改 `var(--surface)/var(--line)/var(--radius-card)/var(--fs-14)`；`extension/popup.css` 调色板数值对齐（扩展打包隔离，保留自己的副本）。
- 死代码：`.btn.small`（0 调用）、`.scholar-snippet-en`（JS 只输出 `<p>`）、`--gold`（0 引用）删除。
- 验证：Playwright 跑 8 个页面 + 1180 窄屏，`overflowX=0`、无 pageerror、help 弹层正常（v2.7.42）；computed tokens 抽查 `--ink-3=#5f7186 / --fs-13=13px / --radius-control=8px / --radius-card=14px / --row-alt=#e5f2ec`；`.page-head h1` 24px/700。
- 回归：Python **478 OK**（exit=0）；Node **235 / pass 235 / fail 0**（插件修 6 条红 + 新增 `tests/design_tokens_2_7_42.test.mjs` 8 条：tokens 存在与链接顺序、style.css 无 `:root`/`--gold`、web/css 无字面量字号与圆角（图标例外）、无 620/650、help 无 token fallback、reader.js 内联无硬编码、死代码消失）。
- 产物：Setup 58,573,197 / `12a71e2c5b81a0770626585e92fef45c0cd00026dc44bcc1cc0bd9294f5defd5`；便携 48,387,143 / `0b6fd5e93f03b9f1adfc2f134c21a79e171e1a75b69433109cf029cd0a65c15b`；Connector 44,097 / `d8f98903433584d170289575d4f7f57c9f5e0d7a1bed1318ad16cd20661ba6f6`；`verify_release --version 2.7.42` 全 PASS（47 个 web 资源）；已装（health=2.7.42）；扩展 diff=0。
- 未做（留到下一轮，避免一次改太多难定位回归）：间距收敛到 4 的倍数栅格、7 套卡片 / 4 套气泡抽公共基类、`style.css` 末尾补丁块重排、说明面板 `--no-launch` 在行尾被断开（一行 `white-space: nowrap` 即可，但需再打一版）。

### 2026-10-30 深夜 2.7.43：第三批 UI（图标 SVG / 状态列 / 阅读器分组 / 可访问性）+ 修引注窗口分类筛选

- **真 bug**：`web/js/word-picker.js` 的 `buildTree` 分类节点只有 `path/name/depth/count/children`，`nodeEl` 取 `n.scope` 得到 `undefined`，点分类实际没过滤（显示全库但高亮像已选）。修复：节点带 `scope: r.path`。实测真实库点「人工智能」160 篇 → 21 篇。
- 图标统一 SVG：`index.html` body 顶部 13 个 `<symbol>`（库/烧瓶/卡片/对话/罗盘/导入/设置/网格/行/复制/星/关闭/搜索），侧栏 7 个入口 + 视图切换 + 卡片操作 + 详情关闭 + 两处搜索框 + 拖拽区全部换成 `svg.ico use`；CSS `.ico` 16px、stroke 1.6、颜色跟随文字，`.icon-btn .ico` 14px。侧栏不再有 `▤ ⚗ ◈ ✦ ⌕ ↧ ⚙` 这类混字体字符。
- 文献库状态列重做：新增 `rowStatusHtml()`，一行 3~4 个 `.st-dot`（`st-ok/st-idle/st-bad/st-run/st-warn`），列头 title 写了图例；`.state-badge` 不再用于行列表。修掉原来 11/12px 两行小字挤在一起的问题。
- 阅读器顶栏分 4 组（`.rt-group` + 分隔线）：`#rt-nav`（返回/前进/页码）、`#rt-search`、`#rt-pages`（上下页/缩放）、`#rt-actions`（侧栏开关/缩放方式/关闭）。`reader-navigation.js` 注入 `#rt-nav`，`reader.js` 注入 `#rt-actions`（都在关闭按钮前）。
- 主按钮层级：`#btn-analyze-all` 从 `btn primary` 改 `btn ghost`，文献库页只剩「＋ 添加论文」一个主操作。
- 可访问性：导航 `aria-current="page"`（goto 里设置/移除）；说明弹层 `aria-modal="true"` + Tab 焦点陷阱（实测连续 Tab/Shift+Tab 都留在弹层内）+ 关闭后回焦；引注页分类树 `role=tree/treeitem`、`tabindex=0`、上下键移动、左右键展开收起、Enter/Space 选中、同步 `aria-selected/aria-expanded`；全站 `prefers-reduced-motion`；`reader.js` 最后一处 `font-size:13px` → `var(--fs-13)`。
- 回归：Python **478 OK**（exit=0）；Node **246 / pass 246 / fail 0**（插件新增 `tests/ui_batch3_2_7_43.test.mjs` 11 条，含分类 `scope: r.path`、13 个 symbol、状态圆点、rt-group 注入点、aria-current、焦点陷阱、reduced-motion、reader.js 无字面量字号）。
- 产物：Setup 58,575,515 / `a3fc5eb97c379002903f7225a287640072974c11b083c43246e37b6ddc98a7ee`；便携 48,390,116 / `9e4ca4222c54a537cc115fa9415312ca9aaa7983ba1969af4258ff9f798018bf`；Connector 44,097 / `a58212521a3917de1f07d7ba0184a0309c0f788dc4a2ad7f07ae7287cf097497`；`verify_release --version 2.7.43` 全 PASS；已装（health=2.7.43）；扩展 diff=0。
- 真机验证（8765）：13 个 symbol、7 个 nav 图标、状态圆点、`btn ghost`、阅读器 4 组 `[3,1,4,3]`、引注页分类 160→21、help `aria-modal=true`；截图 `pw-debug/final-2.7.43/`。
- 仍未做（下一轮候选）：间距 4 倍数栅格、卡片/气泡公共基类、`style.css` 补丁块重排、卡片 `role=button` 内嵌控件的 ARIA 结构、深色模式（tokens 已具备条件，但阅读器与部分弹窗仍有浅色硬编码）、说明面板 code 行尾断行。

### 2026-10-30 深夜 2.7.44：修状态圆点与列头对齐

- 用户截图反馈「状态栏没有对齐」：2.7.43 的状态圆点是 `justify-content: flex-end`（右对齐），列头「状态」是左对齐，同一格里一左一右。
- 修法：`.paper-row-item .pr-status` 改 `justify-content: flex-start`；实测列头 label 与圆点起点同为 x=1185。
- 回归：Node **246 / pass 246**（插件把 newest 改 2.7.44，并在状态列那条补了 `justify-content: flex-start` 断言）；Python 无相关改动（沿用 478 OK）。
- 产物：Setup 58,574,995 / `cbbbee0f38eeb7ec5523bddcb17df0706ef46a798c0ada34e047e44502f786f0`；便携 48,390,678 / `de0a4690f84a68fd2e4c4880e1f492c2bd283ec83cf4652eac6c183b6b59a591`；Connector 44,097 / `bea2b21ccdf0ae5c18271f0a58a16f225a2c302bac7b50f809c78a7f08ac5823`；`verify_release --version 2.7.44` 全 PASS；已装（health=2.7.44）；扩展 diff=0；截图 `pw-debug/final-2.7.44/`。

### 2026-10-30 深夜 2.7.45：文献库状态列固定 80px

- 用户：「我觉得这个状态栏就固定宽度就行」。改法（`web/js/library.js`）：
  - `COMPACT_DEFAULT_COLS` 末位 `130 → 80`；
  - 新增 `COMPACT_FIXED_COLS = { 4: 80 }` + `normalizeCompactCols()`，`readCompactCols()` 与 `applyCompactColumns()` 都会把第 5 列强制回 80（以前拖出来的旧 localStorage 也覆盖）；
  - 列头「状态」的 `<span class="col-resizer" data-col="4">` 删除，只剩 data-col 0–3 可拖；
  - `mousedown` 里加 `if (index in COMPACT_FIXED_COLS) return;` 兜底。
- 实测（装机后 8765）：`grid-template-columns` = `556px 160px 140px 56px 80px`，resizer 只有 `0/1/2/3`，`.pr-status` 宽度 80。
- 回归：Node **247 / pass 247**（插件把 newest 改 2.7.45 并补了固定列断言）；后端无改动。
- 产物：Setup 58,575,157 / `430f8ee4d6178f01151bbc3db6cf501d4791013961259f3b81626e273670e593`；便携 48,389,987 / `271e7fc1a5b19691657ec177a6f84e22fecd77c38ef25e4e2bef18a96936221e`；Connector 44,098 / `623931a44c07b27fa383f305af3efbea7c5d49eba8a6e8ba11326fdf149009e3`；`verify_release --version 2.7.45` 全 PASS；已装（health=2.7.45）；扩展 diff=0；截图 `pw-debug/final-2.7.45/`。

### 2026-10-30 深夜 2.7.46：状态圆点提示改自绘浮层（圆点右上方）

- 用户截图反馈：悬浮在状态圆点时，原生 `title` 提示被鼠标挡住。原生浮层由系统绘制，位置不可控。
- 改法（`web/js/library.js`）：`rowStatusHtml` 把圆点的 `title=` 换成 `data-tip=`，外层 `.pr-status` 去掉 `title`（保留 `aria-label`）；新增模块级 `dotTipEl` + `ensureDotTip/showDotTip/hideDotTip`，document 上 `mouseover/mouseout` 委托 `.st-dot[data-tip]`，scroll(capture)/resize 隐藏。
- 定位：`left = rect.right + 8`、`top = rect.top - tip.offsetHeight - 8`；右侧越界收回到 `innerWidth - width - 8`，上方越界则放圆点下方。
- CSS 新增 `.pw-dot-tip`（fixed、z-index 9600、`pointer-events:none`、深色底白字、`--fs-12`、`--radius-chip`）与 `.show` 淡入。
- 实测（装机后 8765）：圆点 (1259,285)，浮层 (1268,250,90×27)，`above=true / right=true / coversCursor=false`；移开鼠标隐藏。
- 回归：Node **248 / pass 248**（插件 newest→2.7.46 + 1 条浮层断言）；后端无改动。
- 产物：Setup 58,576,557 / `d80f76c3f5146564d8497daeeaf63db8e78d4028998b83f7a5bf3540fb79ae10`；便携 48,390,538 / `5f6d485afa56501bf02718d5693f06cb681a5f5c56b8d1df7fb7629a8513e139`；Connector 44,097 / `ceb868bdcce9b8f92b6a247983c411cb423eb2a0e2a8f40487f5c312f90adaea`；`verify_release --version 2.7.46` 全 PASS；已装（health=2.7.46）；扩展 diff=0；截图 `pw-debug/final-2.7.46/`。

### 2026-10-30 深夜 2.7.47：「详情」开关贴到工具栏最右侧

- 用户：「这个按钮要不就直接融在右边框旁边」——原来 `#btn-detail-toggle` 跟在视图切换后面，工具栏一换行就夹在搜索框和筛选行之间，很突兀。
- 改法（只改 `web/css/style.css`）：`.toolbar` 下新增 `#btn-detail-toggle { margin-left:auto; border-color:transparent; background:transparent; color:var(--ink-3) }`，`:hover/:focus-visible` 只留浅灰底与深色字、无阴影。DOM 位置不变（仍在 toolbar 末尾）。
- 实测（装机后 8765）：按钮右缘 1328 = 工具栏右缘，`gap=0`，`margin-left` 计算值 932px，边框/背景均透明。
- 回归：Node **249 / pass 249**（插件 newest→2.7.47 + 1 条贴右断言）；后端无改动。
- 产物：Setup 58,576,321 / `236a7788363e7f917d45a7f98586958873a96c72aaffc34a76f7f1d9f518b464`；便携 48,391,807 / `301ba1abdf81381d97f42aa3244e6bc46c36fed27eccdeeffca965940985bd59`；Connector 44,097 / `4cb690740aea64e1101abe56a2c7e62730bed422807ffb9858def1ef56fa5332`；`verify_release --version 2.7.47` 全 PASS；已装（health=2.7.47）；扩展 diff=0；截图 `pw-debug/final-2.7.47/`。

### 2026-10-30 深夜 2.7.48：修搜索框放大镜与占位文字重叠

- 用户截图「重叠」：搜索框里放大镜和「搜索标题…」压在一起。
- 根因：`style.css` 末尾共享控件规则 `input:not([type=checkbox]):not([type=radio]):not([type=file]) { padding: 9px 11px }` 特异性 (0,3,1)，压过 `.search-wrap input { padding-left:34px }` (0,1,1)，左侧 34px 被重置成 11px → 文字起点 281、图标 282–298。
- 修法：新增 `.page .toolbar .search-wrap input[type="search"] { padding-left: 34px; }`（(0,4,1)，高于共享规则）。文献库与方法/参数两个搜索框都生效。
- 实测（装机后 8765）：`padding-left=34px`，图标右缘 298、文字起点 304（6px 间距）。
- 回归：Node **250 / pass 250**（插件 newest→2.7.48 + 1 条 padding 断言）；后端无改动。
- 产物：Setup 58,576,234 / `d08d7c271f1dee31a4987d99e78a5360b9ca29079beffee37e5ff7e884e2e374`；便携 48,391,579 / `55e9854af8018febd567ae0115dc05f4a310aebeccb866ee27505695e8ce1ee9`；Connector 44,098 / `47cf01efc3b7e8ad7f9ab26e3cabe95a52447b8514e83e5d6ae399342e86f1ab`；`verify_release --version 2.7.48` 全 PASS；已装（health=2.7.48）；扩展 diff=0；截图 `pw-debug/final-2.7.48/`。

### 2026-10-30 深夜 2.7.49：详情改成「右缘竖条 + 全高抽屉」

- 用户要求：详情不要再占工具栏按钮，做成贴在右边框的细条；展开是一栏与程序同高、完全不影响内部排版。
- 结构：删除工具栏 `#btn-detail-toggle` 按钮；在 `.library-split` 内 `#paper-detail` 之后新增 `.detail-rail`（`id` 不变，仍叫 `#btn-detail-toggle`），内含 `.detail-rail-text`「展开/收起」。
- CSS：`.detail-rail` fixed 于 `right:10px; top:50%`，26×132、圆角 13、白底描边阴影、竖排文字（`writing-mode:vertical-rl`）；`#paper-detail` 改 `position:fixed; top:0; right:0; bottom:0; width:min(380px,92vw)`，`translateX(102%)` + `visibility:hidden`，`.detail-open` 时滑入；`body.desktop-shell` 下 panel `top:36px`、rail `top:calc(50%+18px)`。面板 `padding-right:42px` 给把手让位。删除旧的 sticky/flex 宽度规则与 `.detail-open .library-main` 溢出规则。
- JS：`setDetailOpen()` 移除 `detailBaseWidth` 与 `resize_window`（旧版在桌面会把窗口加宽 380），改为切换 class + 更新把手文案/`aria-expanded`/`title`，关闭时 `cleanupDetailPdf()`。
- 实测（装机后 8765）：rail `[1330,359,26,132]`；开合前后 `.library-main` 宽度都是 **1058px**（零回流）；面板 `[986,0,380,850]` 与窗口同高；`body.desktop-shell` 下 panel top=36、rail top=443；把手文案 展开↔收起、`aria-expanded` 同步。
- 回归：Node **252 / pass 252**（插件按新结构重写详情断言，2.7.47 的旧断言已废）；后端无改动。
- 产物：Setup 58,575,834 / `89b36109635a12551c669005d3ba5c53350c2217c0d1ac9967662f31e3f2aa43`；便携 48,391,361 / `ac466491f2b2f1b331ed8ba9b048336c44e756598e22c47069f855f0850c6336`；Connector 44,098 / `cbf5836567d1a1de94bb035fab60f8d156087b92cd9f226c65cc148ecdc092ea`；`verify_release --version 2.7.49` 全 PASS；已装（health=2.7.49）；扩展 diff=0；截图 `pw-debug/final-2.7.49/`。

### 2026-10-30 深夜 2.7.50：详情竖条圆角改用令牌（消除测试例外）

- 插件指出：2.7.49 的 `.detail-rail` 写了字面量 `border-radius: 13px`，与 2.7.42 定的「除 tokens.css 外不许字面量圆角」守卫冲突；它临时加了「仅 `.detail-rail` 且 13px」的窄例外。按用户拍板选 A：改代码而不是留例外。
- 修法：`style.css` 的 `.detail-rail` 改为 `border-radius: var(--radius-pill)`（999px 在 26×132 元素上会被 clamp 成 13px，视觉等价）。
- 插件侧：删除 13px 例外、恢复严格守卫，newest→2.7.50，补「`.detail-rail` 圆角必须是 `var(--radius-pill)`」断言。
- 回归：Node **252 / pass 252**；`verify_release` 全 PASS；已装（health=2.7.50）；扩展 diff=0。计算样式 `border-radius` 读数为 999px、实际绘制半径 = 13px（与宽度一半相等）。
- 产物：Setup 58,577,492 / `896cc08dba8795b8fe9ed2eef233911c6107a7dada096fdc7b4b52ac0ef1d81e`；便携 48,392,277 / `3c90a7f44040f6413b7f69d17f6316d7c52cb89af5d1d460c36281a66ad8cf87`；Connector 44,098 / `6f53853b0b36970f896535d3207ea1b0d48d7f3271bc8c713543ae2b4ebd61fd`。

### 2026-10-30 深夜 2.7.51：详情面板信息重排（去获取记录 / 元信息逐行 / PDF 预览上移）

- 用户三条要求：①「这个没必要显示」（详情里的状态/获取记录技术信息）②「解决换行的问题」（作者把年份挤到下一行）③「PDF 缩略图移到摘要上面，只留一页大小的窗口可滚动」。
- 改动（`web/js/library.js`、`web/css/style.css`）：
  - 删除 `#detail-fetch-report` 折叠块及其 toggle 处理（`/pdf-fetch/report` 渲染、browser_attempts、失败自动展开）；后端接口保留。`detailStatus()` 已无调用，删除。
  - 元信息改 `<div class="detail-meta"><div class="detail-facts">…`，逐行 `<dt>作者/年份/期刊/卷期页/分类/状态/批注</dt><dd>`；作者两行截断、期刊/分类/卷期页单行省略并带 title。`.detail-meta` 类名保留（`bibliographic_ui` 断言依赖）。
  - PDF 预览 section 移到摘要之前，标题「PDF 预览」；`.detail-thumbs` 固定 `height:min(56vh,430px)` + `overflow-y:auto` + 内边距/描边/圆角；`.detail-thumb` 背景 `var(--surface)`；懒加载 IntersectionObserver root 改为滚动容器 `box`。
- 实测（dev）：facts 行为 作者/年份/期刊/卷期页/分类/状态；headings `[PDF 预览, 摘要]`；无 `#detail-fetch-report`；缩略图窗口 318×430、scrollHeight 6363（32 页）、当前渲染 12 张 canvas。
- 回归：Node **252 / pass 252**；`ai_conversations_ui.mjs` 里旧的 `#detail-fetch-report` e2e 需要插件更新（不在默认 `*.test.mjs` 里）。
- 产物：Setup 58,577,198 / `c58cdf3f8c2515e6386083b85f0ddb17b427b114cef4cf1859995d0150c90fed`；便携 48,391,708 / `b4170ecc0e6733cda12f6c111e7fc5b66b65c18037aad2d2a226dd460efd3af8`；Connector 44,098 / `4c20b5dfd7872891b5dd46917a756f629a713b1e7ed71f7cb645f461ef103fa0`；`verify_release --version 2.7.51` 全 PASS；已装；扩展 diff=0。

### 2026-10-30 深夜 2.7.52：详情状态行再精简

- 2.7.51 的「状态」仍显示 `PDF 已获取 · 索引：已索引 · 精读：已精读`（用户第一张图里那行）。改为 `statusInfo(paper).label`，只显示 PDF 是否已获取；索引/精读看列表圆点。
- 实测（装机后 8765）：状态值 `PDF 已获取`，facts 行 作者/年份/期刊/卷期页/分类/状态，headings `[PDF 预览, 摘要]`，无获取记录块。
- 回归：Node **254 / pass 254**；插件 newest→2.7.52 并补状态行断言。
- 产物：Setup 58,576,805 / `030c3c07144a2f5b0419302e09d6e161176af3669af2daa0e9a2c12e958210ec`；便携 48,392,051 / `2284749e295336afbb4ca9d126d7ffa0718eb167cbf44c9f22068631521a8132`；Connector 44,098 / `b94d21cd5a117240a2170032467d8c96f7b7db8057e64a83de46c5f3180541c9`；`verify_release --version 2.7.52` 全 PASS；已装（health=2.7.52）；扩展 diff=0；截图 `pw-debug/final-2.7.52/`。

### 2026-10-31 凌晨 2.7.53：动效系统 + 自定义强调色（设置项）+ 动效技能

- 用户要求：加动效相关 skill、审查能提升观感的动效、加一些自定义 UI 颜色设置。
- 技能：加载 `animation-principles` + `motion-system`；新建共享 skill **`paperwhale-motion`**（动效令牌、适用场景、reduced-motion、强调色实现约定），attach 到 Chief 与「插件」。
- 动效令牌（`tokens.css`）：`--dur-instant 60 / --dur-fast 120 / --dur-normal 180 / --dur-moderate 260 / --dur-slow 340`，`--ease-standard/decelerate/accelerate/spring`。组件里不再写 `0.2s`/`ease` 字面量。
- 新增动效（`style.css`）：`pwPageIn`（页面切换淡入+上移 4px，180ms）、`pwOverlayIn`/`pwModalIn`（弹窗遮罩 120ms、卡片 260ms）、`pwPanelIn`（任务中心 260ms）；toast、详情抽屉、按钮/卡片/导航 transition 全部改用令牌；进度条 340ms linear。`prefers-reduced-motion` 下 5 个 dur 令牌覆写 1ms，并保留 `.spinner` 旋转（必要反馈）。
- 自定义强调色：
  - 后端：`DEFAULT_SETTINGS["ui_accent"]="#0e7c86"`、`SettingsUpdate.ui_accent`、`PUT /api/settings` 校验 `#RRGGBB`（非法 400「强调色应为 #RRGGBB 格式」）。
  - 前端：新增 `web/js/theme.js`（6 预设 + `applyAccent()` 运行时改 `--accent/-2/-soft/-line/--row-alt/--row-selected`，缓存 `localStorage.pw.accent.vars`）；`index.html` head 内联脚本首屏提前应用防闪；`app.js` 启动与 settings-changed 应用；`settings.js` 新增「外观」组（色块、颜色输入 `#set-ui-accent`、恢复默认、随「保存本组」持久化）。
  - 强调色底/阴影的硬编码 `rgba(20,163,163,…)` 改为 `color-mix(in srgb, var(--accent-2) N%, transparent)`（保留 rgba 回退），导航高亮、焦点环、选中态、任务中心态、PDF 斑马纹都会跟着变。
- 实测（dev + 装机 8765）：预设默认 `#0e7c86`、6 个色块；点「海蓝」→ `--accent=#2563eb`、`--accent-2=#4b80f3`、`--accent-soft=#ecf0f8`，保存后 GET `ui_accent=#2563eb`，刷新仍生效；非法 `"blue"` → 400。动效：3 个 keyframes 均存在，令牌 120/180/260/340ms，reduced 下 1ms。
- 回归：Node **261 / pass 261**（插件新增 7 条：令牌、keyframes、theme.js、settings 字段、后端校验等）；Python **484 项 OK**（接口新增 `ui_accent` 的设置读写/400 校验 6 条）。
- 产物：Setup 58,580,674 / `53fc9865657ed32b95aa92bc5f896bbbf729cf683021c1feba5dc8459be6e567`；便携 48,396,323 / `382ed310b439d8f706eb03331165274f65cc68bada8e5c9a82774c18ec3f5555`；Connector 44,098 / `cbb320f29ee1d28b92ee26dfbf1ab7cda8214fe10516670f1c6c786b08329cd7`；`verify_release --version 2.7.53` 全 PASS；已装（health=2.7.53）；扩展 diff=0；截图 `pw-debug/final-2.7.53/`。

### 2026-10-31 凌晨 2.7.54：整套配色预设（含浅色侧栏）

- 用户：颜色设置不够，主菜单还是深色，参考 `D:\Harness APP\今天练什么` 的调色逻辑，做几个浅色/深色预设。
- 参考实现：该项目的 `Palette`/`ThemeStyle` 是整套色板（base/panel/card/control/hairline/三级文字/accent + 深浅语义色），每个预设一整套；浅色主题连侧栏一起变浅。
- PaperWhale 落地：
  - `tokens.css` 新增 `--shell-*` 外壳令牌（bg/bg-mid/bg-2/bg-3、text/strong/muted/faint、nav/nav-strong/pill、chrome、line/line-strong、hover 三档、menu-bg、danger、active-text）。
  - `theme.js` 重写为 8 套 `THEME_PRESETS`：深色外壳 深海/石墨/紫夜；浅色外壳 纸白/米白/青瓷/樱粉/日光。每套含 shell + content（ink/bg/surface/line 等）+ 默认 accent；`applyAppearance({theme,accent})` 一次写入全部变量并缓存 `pw.appearance.vars`；`applyAccent` 保留兼容。
  - `style.css`：侧栏、自定义标题栏 `.tb-*`、阅读器顶栏、任务中心按钮的硬编码深色全部换 `--shell-*`；`.setup-banner` 由冷蓝渐变改 `--accent-soft/-line`；brand-mark 走 accent color-mix；新增主题预设卡片样式（chip 用斜切展示外壳/内容色 + accent 点）。
  - `word-picker.css` 顶栏换 `--shell-*`；`word-picker.html` 也加首屏缓存脚本（引注页会跟随当前主题）。
  - `settings.js`：外观组增加 `ui_theme` 隐藏字段与 8 个预设按钮；选预设会同步默认 accent；保存时 `ui_theme` + `ui_accent` 一起写；`app.js` 启动与 settings-changed 调 `applyAppearance`。
  - 后端：`DEFAULT_SETTINGS["ui_theme"]="midnight"`、`SettingsUpdate.ui_theme`、`PUT /api/settings` 校验 `[a-z][a-z0-9-]{0,31}`（非法 400）。
- 实测（装机 8765，预览不写入真实设置）：8 个预设、默认 midnight；纸白 shell `#eef2f7`、米白 `#efe8dd`+amber、青瓷 `#e2ede8`+teal；保存 `ui_theme=paper` 后刷新仍保持；旧 `pw.accent.vars` 作为回退继续读。
- 回归：Node **263 / pass 263**（插件新增 2 条主题预设断言）；Python **489 项 OK**（接口补 `ui_theme` 5 条）。
- 说明：这一版只做「外壳/内容配色的浅色与深色组合」，内容区始终是浅色阅读面；完整深色内容模式（所有卡片/表格/阅读器都翻黑）留到以后。
- 产物（最终重打）：Setup 58,584,646 / `cc1690ec28646e9726180be098d1215db9af28dcf3d88202c1b1e370de56077c`；便携 48,400,018 / `3915cfad479d129c5f6ba2da6c6009dc0c378b53bb6a45302b5c48777dd0b4eb`；Connector 44,098 / `1c4d4a0e05018991d09e4d0b5ad05d4b93d423dc5f1b8e49fb02ef46409afb81`；`verify_release --version 2.7.54` 全 PASS（48 个 web 资源）；已装（health=2.7.54）；扩展 diff=0；截图 `pw-debug/final-2.7.54/`。
















### 2026-11-01 2.7.55：审查改进（源码完成，未打包/安装）

- 依据 `docs/ui-review-2026-11-01/review.md`，已获用户“没问题，改”授权，完成导入、阅读器、任务反馈、性能及页面整理。详细实现/验证见同目录 `implementation-plan.md`。
- 导入：实际检查 PDF 是否存在，丢失后重导可修复；SQLite BEGIN IMMEDIATE 原子查重、插入、补标识，兼容历史重复；DOI/arXiv 变体归一匹配；PDF 解析不占写事务；新增默认排序索引。
- 阅读器：标签键盘处理去重；流式增量更新，尊重上滚、保留思考展开/引用/操作；回到底部入口；侧栏拖动 rAF 合并，结束保存与 PDF 重排；焦点恢复与后台 inert，支持独立/内部弹窗。
- 任务中心：轮询所有未完成批次并跟踪旧批次最终状态；按 batch_id 更新，保留详情、按钮焦点和滚动。
- 页面：标签/分类缓存按 library-changed 失效、手动刷新强制取新；无 PDF 详情预览紧凑；邮箱配置默认折叠；方法待提取紧凑行；设置分组导航及常用组前置；剪贴板提示和八套主题辅助文字对比度修复。
- 主代理验证：全部 Node *.test.mjs 269/269；导入9/9，任务+workspace 后端20/20；review_improvements_ui、workspace_ui、ai_conversations_ui 全通过；相关语法和 diff 空白检查通过。均为临时库/模拟 AI/隔离 Edge。
- 保留现有状态列固定80px、其他四列自由调宽、标题默认两行与默认联网/思考。workspace_ui 的旧五列断言已按现有固定状态列设计更新。
- `APP_VERSION`、关于、build.ps1 默认与 README 示例更新2.7.55。**没有生成exe、没有安装替换、没有触碰真实用户数据。** 用户下一次要求打包时再执行构建与发布验证。


### 2026-11-01 2.7.55：打包完成（未安装替换）

- 用户要求“打包”，执行 `build.ps1 -Version 2.7.55` 成功；Connector manifest 同步2.7.55。构建日志 `pw-debug/build-2.7.55.log`。
- `tools/verify_release.py --version 2.7.55` 全部 PASS：ZIP CRC、x64 EXE、49个web资源、随包skills、发布文档、无运行时/私人数据、安装payload、64个后端模块、检索/PDF集成、Connector一致性及所有SHA256。
- `release/PaperWhale-2.7.55-Setup.exe`：60,658,660 bytes；SHA256 `16eabc868191a7f05659d0126ca38c29880bdfe478171bc08b67bda7e61dfb17`。
- `release/PaperWhale-2.7.55-win-x64-portable.zip`：49,446,783 bytes；SHA256 `43fd6a495dcf60afb2f907bb23704615e43c2e541607d7b54c6bb7da3592921f`。
- `release/PaperWhale-2.7.55-Connector.zip`：44,098 bytes；SHA256 `7f27816cd21d095e7c0eda39dba14bfcb782d5f5afac3659d9e4cfcf72797bd2`。
- 本次只生成与校验发布产物，未运行安装器、未替换已安装程序、未启动真实用户库。


### 2026-11-02 NPL 2.7.56：正式品牌与新 Logo 落地、打包完成（未安装）

- 用户确认名字 Nexus Paper Library（NPL），选择 `exec-49dc2d8a-f4ad-482a-be92-78de73966ca8.png` 并要求 L 底边延长到右端面（选定展示 `assets/branding/npl-selected/npl-brand-board.png`），随后要求打包进程序及替换所有“鲸”相关自称。
- 生产源文件：`web/brand/npl-mark.svg` 与 `npl-app.svg`，按选定几何形状落地，L 底边与右侧对齐；`tools/render_brand.mjs` 用隔离 Edge 渲染透明 PNG，`make_icon.py` 统一生成 assets/npl.png、npl.ico、favicon 和浏览器16/32/48/128图标。旧开发图标别名也替换为新图形；安装包只带生产资源，不带设计展示板。
- 可见名称/文案：主窗口/侧栏/标题栏/空状态/AI提示/帮助/安装器/托盘/快捷方式/浏览器Connector/Word Ribbon均改NPL。运行时文件与发布说明不再出现鲸形emoji或相关自称。
- 保持内部兼容：PaperWhale.exe、APPDATA/PaperWhale、paperwhale.db、模块/API/PW事件/存储keys、Word模板PaperWhaleMacros.dotm及旧设置键、卸载注册表键不变。health.app保持旧技术发现标识PaperWhale，新增product_name=Nexus Paper Library，旧Connector不受影响。
- 安装器新快捷方式名Nexus Paper Library；旧快捷方式只按实际目标归属清理，同名其他应用快捷方式保留。未运行真实安装/卸载操作。测试覆盖内存mock所有权、同名处理和PowerShell脚本解析。
- Word模板已实际重建（73040 bytes），使用独立PID的Word进程与临时无宏RTF绑定，禁用宏执行，不操作用户STARTUP/偏好设置；重新读取VBA模块校验最新源码与NPL Ribbon一致。构建脚本改为此安全隔离流程，并修复重复ZIP部件注入。
- 版本：APP_VERSION/关于/Connector/build默认/README为2.7.56；外部产物名统一NPL-*，内部技术exe保留兼容名。Connector ZIP根目录为NPL-Connector。
- 验证：Node全部 *.test.mjs 271/271；主代理后端品牌+导入+任务+workspace31/31，Word模板7/7；代理另跑相关功能111项通过。npl_brand_ui覆盖7页面+阅读器，所有品牌资源200且无pageerror；ai_conversations_ui八组及workspace_ui全通过。fixture已关闭剪贴板监听，避免读取真实剪贴板。修改文件diff检查通过。
- 发布完整性：`tools/verify_release.py --version 2.7.56` 全PASS，54个web资源、64后端模块、托盘/安装器/Connector Logo、NPL Ribbon、CRC、skills、文档、无私人数据及所有SHA256。Windows CArchive路径兼容斜杠/反斜杠。构建日志与截图在 `pw-debug/npl-2.7.56/`。
- `release/NPL-2.7.56-Setup.exe`：60,764,834 bytes；SHA256 `43c7adb71c87e5f75dec157b914eab2fd8db2726c470bdc602e3a1e7d3037442`。
- `release/NPL-2.7.56-win-x64-portable.zip`：49,534,404 bytes；SHA256 `27c00640866669570a640902ffc2997885df498d97ed1fbbe38fd0fb749ecb39`。
- `release/NPL-2.7.56-Connector.zip`：49,278 bytes；SHA256 `86a398d2ed338ea3824a0f41d87c913f9755b274c52a4994ba5a6e37fb4a682c`。
- 未安装替换现有程序，未改真实库/邮箱/模型设置。升级安装前关闭Word，浏览器扩展安装后重新加载。


### 2026-11-02 NPL 2.7.57：标题后缀与任务栏图标修正（已打包，未安装）

- 用户反馈标题不需要“(NPL)”，任务栏仍显示旧图标。已将HTML标题/自定义标题栏/关于提示统一为 Nexus Paper Library。
- 只读核对正在运行的 D:/论文鲸/PaperWhale.exe 与2.7.56 dist exe SHA256相同，确认用户已在运行新版。pywebview WinForms 原先走ExtractIconW(sys.executable) fallback，没有显式窗口ICO；安装快捷方式也指向同路径EXE图标。
- 修复：desktop._window_icon_path() 查找随包assets/npl.ico（fallback web/brand/npl.ico），webview.start显式传入icon；启动UI前设置独立进程任务栏AppUserModelID NexusPaperLibrary.Desktop。快捷方式优先指向 _internal/assets/npl.ico；build额外打包该ICO。未清空系统图标缓存或修改用户固定栏。
- 验证：隐藏原生WebView2窗口（独立临时profile，无服务/真实库）读取Form.Icon转PNG，与NPL ICO逐像素一致（32x32）；实际进程AppID与窗口标题正确。结果 `pw-debug/npl-2.7.57/native-icon.json`。测试 `tests/native_taskbar_icon_probe.py`。
- 单元回归 tests.test_desktop_identity + tests.test_npl_branding 5/5；全部Node *.test.mjs 271/271；npl_brand_ui七页与阅读器通过，无缺失品牌资源或pageerror。标题已无括号后缀。
- Microsoft API依据：https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nf-shobjidl_core-setcurrentprocessexplicitappusermodelid ，启动UI前设置任务栏身份。
- build.ps1 -Version 2.7.57成功，verify_release --version 2.7.57全PASS（含原生窗口/快捷方式ICO与标题检查）。日志 `pw-debug/npl-2.7.57/build.log`。
- Setup：60,805,288 bytes / SHA256 `7873d1b3df5fce1fc2f8cf72a18a5e8d103a2c2a40582e1c1450bf250b622e8a`。
- Portable：49,574,829 bytes / SHA256 `073b6f84d7c5e9bc944e6b46578b4ddcc25a87ed343b00932ded651e02fd2c70`。
- Connector：49,278 bytes / SHA256 `6e6e895bf837dfe71307f74f7939841e85e7cea770251df8f7fbe4e54b17b2fe`。
- 对旧版手动固定的快捷方式，如升级后仍显示旧图标，可取消固定再固定；未自动操作当前用户任务栏或安装替换。真实数据路径/插件设置兼容保留。


### 2026-11-02 NPL 2.7.58：最终留白 N 与启动开屏（已打包，未安装）

- 用户最终指定参考 `C:/Users/10498/AppData/Local/Temp/codex-clipboard-d4511b96-025d-40ac-b355-2c360cdf9445.png`：负形留白 N，上部两条弧形书页略延长且不超左右边缘。生产 SVG 五条 data-part 路径统一，颜色按最终参考为 #6e0342（非较早 #78006e）。PNG、ICO、托盘、浏览器插件多尺寸图标及兼容别名已同步生成。
- 按用户指定 onetake 技能编排启动动画，约2.6秒（1480ms构成+320ms静止+800ms左右揭开），沿书脊分为左页、右页与字标；同一批元素持续展开，底下是正在初始化的真实主界面。无音效。`tools/compose_startup.mjs` 在开发阶段使用已安装技能的纯函数采样出 `web/js/startup-motion.js`，没有把技能库/字体/音效复制进产品。说明 `docs/startup-animation.md` 随包分发。
- `desktop.py` 每次 main 生成 UUID32hex，通过原生窗口 URL startup 参数进入；head 同步消费并移除参数+sessionStorage去重。普通浏览器/Word picker、刷新、页面导航、阅读器及托盘恢复不重播。支持 Esc/跳过、系统减少动画、运行中偏好变更；保留标题栏按钮可用。
- `app.js` 对装饰性启动模块使用受保护动态 import，避免动画 JS/数据文件缺失导致整个 app 静态依赖链失败；样式缺失自动跳过。独立6秒超时解除遮挡。主程序并行加载，慢请求最迟约5.3秒展开。阅读器提前打开时先释放启动层焦点/inert，避免关闭阅读器后主界面被锁定。
- 验证：271项Node现有测试、6项Python桌面/门控/品牌测试通过。`tests/startup_ui.mjs` 11组实际UI场景通过，含只播一次、刷新、新启动标记、导航、跳过、减少动画、早开阅读器、确定性seek、多尺寸980–1920、分别缺动画模块/数据/CSS仍可正常操作。`tests/npl_brand_ui.mjs` 七页+阅读器无旧品牌、缺失资源或页面错误。
- 预览：`assets/branding/npl-startup/NPL-startup-preview.gif`；实时录像 `pw-debug/npl-startup/NPL-startup-preview.webm`，关键帧与记录同目录。测试均独立临时数据及独立headless Edge，未安装或替换用户正在运行的应用。
- build.ps1 -Version2.7.58 成功；verify_release.py --version2.7.58 全PASS（57个web资源、64个编译后端模块、包/插件/全部摘要匹配）。
- 安装包 `release/NPL-2.7.58-Setup.exe` 60828268字节，SHA256 `0f39a489bbdfceea53b23a7f645f61cd46b84daf4ca3cacdb4c86a758cf9a04a`。
- 便携包 `release/NPL-2.7.58-win-x64-portable.zip` 49595736字节，SHA256 `32a49456452f1eba601e35e4e94a8d48e12ba368545a9b53e6a8a5fc5935e20d`。
- 插件 `release/NPL-2.7.58-Connector.zip` 49716字节，SHA256 `c40540f0a62c4a42839cc6ea8ef94fe3463311149b4866b294cc811208556b74`。内部 exe/API/PW键/数据路径/Word模板仍保留兼容标识。


### 2026-11-02 NPL 2.7.59：光线环绕生成开屏（已打包，未安装）

- 用户认可2.7.58，但要求“几个线条炫酷的环绕生成图标”“更加灵动”。继续使用 onetake 制作，不更改最终静态Logo。
- 三条酒红/玫紫/冷蓝光线错峰穿行，分别落向书页真实转角；五条生产路径的描边被带出，渐收成实体。图标开始位于画面中央，0.58–1.38s轻滑向左与后到字标汇合。完整标志1.88–2.20s静止，再0.8s左右揭开，总3.00s。
- `tools/compose_startup.mjs` 生成22条采样关键帧。光线头位置按SVG路径初始化采样，播放无JS逐帧循环；光线/光头/描边在静止段均归零，不遮盖最终图形。不增加运行依赖；启动去重、跳过、减少动画、故障降级继续沿用。
- `tests/startup_ui.mjs` 更新为新时间轴，12组实际UI检查全过（新增3条光线先于实体且最终完全收束）；271项Node测试、3项桌面/门控Python测试通过，无页面错误。预览与测试记录 `pw-debug/npl-startup-orbit/`。
- 新动图 `assets/branding/npl-startup-orbit/NPL-startup-orbit.gif`（104帧/1792330字节）；上一版预览保留在原目录。
- 2.7.59构建成功；verify_release全PASS，包含57个web文件、64个后端模块、安装器和便携包资源/摘要一致；未运行安装器或触碰真实库。
- 安装包 `release/NPL-2.7.59-Setup.exe` 60834436字节，SHA256 `d4c256287d21b32f80065f3d9f1429a3e3fe06adfb99cec85c8ef2558cc7540f`。
- 便携包 `release/NPL-2.7.59-win-x64-portable.zip` 49601723字节，SHA256 `26f10d9bc38da47b829dadc6ea1a70c4a0038619ef7517658f050fef100977c8`。
- Connector `release/NPL-2.7.59-Connector.zip` 49716字节，SHA256 `e779b5c53d02b6f65c654218c07026268ea72ab3d458b50428d2ca96bc84af07`。


### 2026-11-02 NPL 2.7.60：阅读器标题栏禁用与详情抽屉遮挡（已打包，未安装）

- 用户截图指出论文阅读页顶部菜单/窗口关闭全部失效，右侧详情抽屉无法操作，要求进入阅读器自动收起右栏。
- 复现：阅读器 accessibility 背景隔离给 body 同级的 app-titlebar 与 tb-resize 加 inert，原生菜单和窗口按钮被禁用；文献库详情面板 z950 高于阅读器 z100，被 inert 禁用后仍绘制于阅读器之上。
- 修复：背景隔离排除原生标题栏/resize和独立 aria-modal 对话框；标题栏及独立帮助弹窗里的键盘操作不再被阅读器焦点陷阱抢走。原生菜单导航会关闭阅读器再切页。
- openReader 发送 reader-opening 事件，由 library 关闭详情抽屉并清理缩略图；setDetailOpen 阻止阅读期间异步详情请求重新打开抽屉；CSS 同步隐藏详情与把手，覆盖收起过渡期间的遮挡。阅读器自身AI面板仍可手动折叠。
- tests/reader_overlay_ui.mjs 新增故障复现/回归：修复前标题栏inert断言失败；修复后验证顶部五菜单、帮助打开/关闭、min/max/close桥接、页码翻页、阅读AI侧栏折叠、读者关闭、详情自动收起/迟到请求禁止重开/退出后可重新展开、原生菜单导航至设置，全部通过。独立临时库+headless Edge，无用户库或真实窗口操作。
- 271项现有Node测试、完整reader_interactions_ui.test.mjs通过。新版本构建与 verify_release全PASS（57web资源/64后端模块/摘要一致）；未安装到用户正在运行的程序。
- 安装包 release/NPL-2.7.60-Setup.exe，60835183字节；SHA256 f59c20a896a6f343cf41d75d411cb6652a633914600277e6ab27686b3daea0d0。
- 便携包 release/NPL-2.7.60-win-x64-portable.zip，49603177字节；SHA256 9463d6a4fb46b9d7ca9c7f5a3915d783d36c321c4532c10e7761dac231bbbbd8。
- 插件 release/NPL-2.7.60-Connector.zip，49715字节；SHA256 6e8cb3712aa04a9ca6cce03b8a5577db4dbdef6e8d1e9b85bb33fea1123e0994。
- 构建/验证日志 pw-debug/reader-fix-2.7.60-{build,release}.log；Node日志 pw-debug/reader-fix-2.7.60-tests.log。


### 2026-11-02 NPL 2.7.61：就业资讯首版（已打包，未安装）

- 用户要求加入就业资讯，并选定科研招聘（高校、研究所、博士后、光学光电企业研发）与企业就业（校招、社招、实习）为主。实现独立“就业资讯”页面与原生查看菜单入口，未扩展公务员等招考。
- 普通搜索不依赖模型，复用项目 DDGS→Bing 公开搜索；AI 先按自然语言和就业偏好规划最多3个查询，再对实际返回来源按1-based index匹配；新条目只从真实搜索池产生，模型生成的URL/未知索引不会成为候选。当前年及次年辅助查询；结果明确是摘要线索，有效期/申请条件需原公告核实。
- 单独 employment_items 表保存URL去重、分类、待处理/收藏/已投递/忽略、备注与时间；规范链接去追踪参数但保留 hash 路由，避免破坏招聘SPA职位ID。重复检索保留处理状态/备注。支持手动保存招聘链接、分类/状态/文字筛选；不自动投递或定时采集。
- employment_preferences 存在DB settings中；就业背景仅本入口使用，研究画像需用户勾选才引用。career 独立会话，复用现有实时限高分析、停止、清空/撤销清空；不读论文全文或其他入口历史。上轮真实来源元数据支持后续指代；保存结果在事务中核对会话id/revision，已清空或停止的旧结果不能晚到写入。
- 新 router/service: paperwhale_server/{routers,services}/employment.py；前端 web/js/employment.js 与 css/employment.css；说明 docs/就业资讯说明.md 随包分发，设计 docs/employment-design.md。
- recovery备份表与设置白名单加入就业数据，旧备份无employment表仍可导入。修复紧凑侧栏旧标签裸文本导致竖排的问题：所有8个入口使用 nav-label + aria-label/title，沿用既有窄屏图标模式。
- 验证：11项就业单元/API测试；联合原有workspace/conversations共57项Python通过。271项Node通过（旧断言七个导航按新需求更新为八个并检查就业入口）；tests/employment_ui.mjs覆盖普通/AI、来源、备注状态持久化、清空不删线索、手工链接XSS转义、1040px布局；reader_overlay_ui回归通过。真实公开网页检索返回5条来源，不依赖用户资料；自动化UI使用隔离临时库/明确标注的模拟招聘，未修改真实库。
- 构建2.7.61成功；verify_release全部PASS（59个web文件、66个编译后端模块、安装器/便携/Connector/全部摘要一致），未安装到用户应用。日志与截图 pw-debug/employment/。
- release/NPL-2.7.61-Setup.exe：60857423字节，SHA256 8cc8a08ec8075285f1685e2cb56364a577319d5b528b9f4e314b933a46e7b1d0。
- release/NPL-2.7.61-win-x64-portable.zip：49626055字节，SHA256 1308b8715efdcd8bb4c7677effe18d3d9a53e902ad0fbaa89addf798d86ef5ec。
- release/NPL-2.7.61-Connector.zip：49715字节，SHA256 9420bd481e8530ff8412c6feceadb69844baf823f87d18f13451557d22b62d59。


### 2026-11-02 NPL 2.7.62：就业检索相关性与来源核查（已打包，未安装）

- 用户用“帮我找长三角地区与偏振成像、光电系统相关的研发岗，以及高校和研究所招聘”复现普通搜索返回帮字词典、AI返回泛招聘页和缺乏方向/有效性依据的条目。注意截图中的北大公告是博士后招聘，不能错称博士招生；本次仍允许真正博士后就业招聘。
- 根因：普通检索直接把整句口语+后缀作为唯一查询；AI也保留此无效原始查询并仅按摘要宽松选择；没有正文技术/工作地点/有效期检查，招聘目录与具体职位不分。
- 新服务 `paperwhale_server/services/employment_search.py`：解析方向/地区/类别，长三角不作为单位名原样检索，拆成上海/苏州/杭州/合肥及高校edu.cn、研究所cas.cn查询；最多6组并行3路检索，交错汇入+机构域名优先，避免首批平台网页占满正文名额。最多18个具体来源+6个入口；入口不浪费正文读取名额。
- 读取公开HTML有7秒HTTP超时、1MB/24000字符限额、每跳redirect和DNS全地址公网校验、不自动登录。提取岗位职责/任职要求/研究方向等正文证据；单位简介技术词不替代职责，单位名地名不替代工作地点，明确不符方向/地点、已过截止或标记结束的招聘不放入匹配结果。词典/百科/文库/学生招生前置排除；保留博士后招聘。正文不可读/登录验证、方向相近、地点不明、较旧公告等进入待核实；无充分依据时不降低门槛凑数。
- AI规划和筛选均受确定性检查约束，不能把review升级matching，也不把AI对待核实条目的自信断言显示成匹配理由。已核查不是保证在招，未知截止时间仍明示未核实。
- 新 employment_checks 表保存来源核查元数据，不更改既有employment_items列；备份恢复增加可选表，2.7.61及更早备份兼容。原有未核查条目作为legacy展示，收藏/投递状态和备注保留；未操作真实用户数据库。
- 页面主列表显示有来源依据的岗位/公告及手工保存项；入口、相近方向、未核实与旧线索进入折叠区。显示核查日期、可核实地点/发布日期/截止日、正文节选及本轮真实查询。输入过泛时返回具体关键词引导。
- 验证：截图故障回归先失败后通过；就业质量/API/旧备份/工作区共50项Python通过，271项Node通过；employment_ui含旧线索折叠且保留备注的实际浏览器回归通过。真实公开检索原句复测从同一研究院转为多个单位来源；在严格方向+明确工作地要求下复测未确认匹配岗位（0），所以其余真实来源归待核实/入口，未声称已经找到有效岗位。记录 `pw-debug/employment/search-quality-live*.json`；没有使用用户API Key作真实模型调用。
- 构建2.7.62成功，verify_release全PASS（59web资源/67后端模块/安装器与便携及插件摘要一致）；未安装。日志 `pw-debug/employment/2.7.62-{build,release-verify,node-tests}.log`、`search-quality-tests.log`。
- 安装包 release/NPL-2.7.62-Setup.exe，60886904字节，SHA256 3135ac7bd6a6815488849441ef714e59a835edf6515424ffb3f23429f165d4b3。
- 便携包 release/NPL-2.7.62-win-x64-portable.zip，49655007字节，SHA256 91753fbdeef96d2b4db3e3606454fa9df6cb9ee777547d29bd45642826071574。
- 插件 release/NPL-2.7.62-Connector.zip，49715字节，SHA256 0d67a514258d41d0b269dc6dcf2bcd9f22c0cbd5ca863df0514e286a303f2971。
- 一次旧备份测试因fixture sqlite连接未close导致Windows临时库清理失败，已修测试关闭方式并重跑通过；旧残留临时目录 `C:/Users/10498/AppData/Local/Temp/npl-employment-rel6di_g` 的可选清理被工具策略拒绝，未重试删除。与产品数据和交付无关。


### 2026-11-02 NPL 2.7.63：就业偏好常驻展开（已打包，未安装）

- 用户要求就业偏好的填写区域直接展示。web/index.html 将折叠details改为带标题的常驻section，直接显示关键词、地区、学历/技能/目标、画像引用勾选和保存按钮；web/css/employment.css 增加分隔线和标题样式。未改字段ID、保存逻辑或画像引用默认值。
- 现有employment_ui回归通过，已视觉检查1500px与1040px截图，偏好字段直接可见。构建2.7.63与verify_release全PASS（59web/67后端模块），未安装或操作真实用户库。
- release/NPL-2.7.63-Setup.exe：60883893字节，SHA256 7b42065f490ab34887659ae497e2b1689dc721cd535be939b0aa88021163b1b7。
- release/NPL-2.7.63-win-x64-portable.zip：49653012字节，SHA256 d94a3b46ede62601eeb7cbd1cc054e0afea80550ac9405850935d80800c61aab。
- release/NPL-2.7.63-Connector.zip：49715字节，SHA256 6150c105e8d9cc93d589ed73645768c4ad0e67f257d0f3a3cb42c4971e92d251。日志与截图pw-debug/employment/。


### 2026-11-02 NPL 2.7.64：按用户要求撤下就业资讯（已打包，未安装）

- 用户明确“这个板块好像不太需要，撤掉吧”。移除侧栏入口、整个页面、CSS/JS加载与初始化、顶部查看菜单、后端路由注册。
- 删除web/js/employment.js、web/css/employment.css、routers/employment.py、services/employment.py及employment_search.py，退休对应就业专用API/质量/UI测试及fixture；其他文献模块不变，窄屏nav-label修正继续保留。随包docs不再包含就业资讯说明，仓库历史设计/说明仅作存档。
- 未清除用户数据：employment_items/employment_checks表、employment_preferences备份白名单和可选表恢复兼容继续保留；career作用域仅为历史对话数据兼容，界面无入口、检索API已不存在。
- 验证：271项Node通过；七个文献页面+阅读器branding UI、reader_overlay_ui通过，无缺失资源/页面错误；18项workspace备份/恢复等测试通过；隔离临时数据API检查 employment/items与search均404，页面无employment入口。构建/verify_release全PASS，另外确认ZIP不含就业JS/CSS/说明、编译包不含就业服务模块（57web/64后端模块）。未运行安装器。
- release/NPL-2.7.64-Setup.exe：60835873字节，SHA256 9712c8282beb0e6282537e924eee835c4a96e77d523a9924661a981e4c8fed82。
- release/NPL-2.7.64-win-x64-portable.zip：49603767字节，SHA256 8e05557ead2baa3fe94561a98d9f6d3067dd174995ec68c1735b3f51d0b2fd61。
- release/NPL-2.7.64-Connector.zip：49715字节，SHA256 ac337c5685b48840d977df66cc67b73816b701717fd375a3f775a4001e15fa42。
- 日志 pw-debug/2.7.64-{build,release-verify}.log、pw-debug/employment/2.7.64-{node-tests,backup-tests}.log。当前应从文献功能继续，不再推进就业板块。


### 2026-11-02 NPL 2.7.65：期刊字段污染清理（已打包，未安装）

- 用户截图期刊为“72 物理学报 DOI 10.7498/aps.72.20230934 …”。期刊原站 https://wulixb.iphy.ac.cn/article/doi/10.7498/aps.72.20230934 确认：物理学报，2023，72(22):224202；论文标题为“基于倏逝波照明的空间移频超分辨成像技术研究”。没有按截图去擅自修改标题或作者。
- 根因：metadata.clean_meta此前仅归一卷期页，venue直接照收；精读模型约束不足，非空污染值后续补全通常不纠正。
- 新services/bibliography.py纯离线clean_venue_fields，按显式DOI、Vol/Issue、卷(期):页码、中文期刊前置卷号分离；分离值仅补空，不覆盖已有明确字段。保留2D Materials/3 Biotech/4OR、会议年份、英文缩写句点等正常名称。
- metadata.clean_meta、ingest首次落库、db.update_paper统一校验；更新时额外查已有卷期页/DOI，不让由venue推断出的值覆盖数据库显式值。精读venue提示明确禁止混入卷期页/年份/DOI。
- db.init_db在原有迁移后离线清理已有明显污染venue，保留id/阅读数据/时间排序；paper_to_dict对历史脏数据也作读取兜底。未对用户正在运行的真实数据库执行清理；安装新版启动后生效。
- tests/test_venue_cleanup.py截图回归先失败后通过；连同bibliographic_fields/ingest_atomic共37项Python通过，analyze_metadata在PAPERWHALE_LLM_MOCK=1下9项通过（初次未开mock报未配置，非回归失败）；271项Node通过，七个页面和阅读器UI无错误。
- 构建2.7.65和verify_release全PASS（57web资源/65后端模块/全部摘要一致），未安装。日志 pw-debug/venue-{cleanup,analyze}-tests.log、pw-debug/2.7.65-{build,release-verify,node-tests}.log。
- release/NPL-2.7.65-Setup.exe：60841289字节，SHA256 692a7acf81c8173d55c5a10b46be6f19d89531f108c84514543472a9501b5815。
- release/NPL-2.7.65-win-x64-portable.zip：49609816字节，SHA256 527cbad25118183f9a145e81df5dfe9a5dec45822897d98ba1adc317a00f3821。
- release/NPL-2.7.65-Connector.zip：49716字节，SHA256 7831121a57fe47729639a37b61afdf30f72071cedc402e351477c2cb1e3b26b7。

### 2026-11-02 NPL 2.7.66：真实界面新手指引（已打包，未安装）

- 用户要求类似游戏指引：真实界面框选功能，介绍卡片和下一步自动切换页面/功能。新增 web/js/onboarding.js、web/css/onboarding.css；主指引13步覆盖导航、模型连接、研究画像、PDF与链接导入、文献筛选与阅读、论文发现、跨论文问答、方法参数、复习卡片、插件与添加入口。高亮真实DOM，自动导航/滚动；上一步/下一步/跳过/完成与Esc、焦点循环及窄窗定位。
- 首次使用等待启动开屏结束，不与开屏/其他模态叠加；旧pw-setup-dismissed=1不自动弹出。localStorage npl.onboarding.v1记录started/completed/skipped/interrupted，当前来源一经展示不会每次启动重复；手动入口不受此限制。主指引完成后第一次reader-opened触发4步阅读指引，独立key npl.onboarding.reader.v1；主指引不会打开用户论文，空库也能完成。清除网页存储/服务地址变化可能重现导览。
- 手动入口：标题栏帮助→新手指引，设置→关于→重新开始新手指引，阅读器顶部→阅读指引。帮助入口在阅读器中会介绍当前阅读器。新增page-changed、reader-opened事件衔接；原reader-opening继续负责详情栏关闭。标题栏/resize位于导览之上，菜单导航结束导览；阅读指引Esc只退出导览不关闭论文。无需第三方动效依赖，使用既有motion/color/radius/type令牌；减少动态效果跳过WAAPI入场。可选导览CSS缺失时直接放行工作台。
- 导览不导入、不更改配置、不启动AI任务；发现页自身初始化可能正常创建空研究话题。说明docs/新手指引.md已加入release-docs.json随包。未操作真实用户数据或安装到正在运行的程序。
- 验证：tests/onboarding_ui.mjs隔离Edge浏览器8组检查通过（开屏衔接、13步/回退/滚动/1040宽、4步阅读/独立Esc、完成持久化/手动重看、原生关闭/菜单导航、空库/减少动态、旧版稍后标记、缺CSS放行），无页面错误；startup_ui 12组、reader_overlay_ui、7页+阅读器branding UI通过；全部271项Node通过。日志pw-debug/2.7.66-{onboarding-ui,startup-ui,reader-ui,brand-ui,node-tests}.log，截图pw-debug/onboarding/。首次全测仅因更新日志顶层标题未匹配新版本失败，修正标题后完整重跑通过。
- 构建及verify_release全PASS（59web资源/65后端模块、安装器内嵌payload和ZIP资源/版本/摘要一致），日志pw-debug/2.7.66-{build,release-verify}.log。未运行安装器。
- release/NPL-2.7.66-Setup.exe：60849764字节，SHA256 69cbee0980aec68ea54c084b5ea532ce36eb2877bd53081b4c85a24873e66c26。
- release/NPL-2.7.66-win-x64-portable.zip：49619090字节，SHA256 2e2b94425a65b83ab6b5890e7518fd9f24400aaec297102569128f95b513d819。
- release/NPL-2.7.66-Connector.zip：49719字节，SHA256 76654d9df0abad7df840f5ac0761df4ee2c2e84813b91766ccef3a2f65af0475。

### 2026-11-02 NPL 2.7.67：设置分区导航不再覆盖表单（已打包，未安装）

- 用户截图设置导航悬浮在中间、上方露出被截断表单。根因settings-navigation使用sticky且与所有表单共用.page滚动区。改为设置页active时flex纵向布局：标题/测试连接/保存与分区导航在正常文档流顶部，下方新.settings-content独立滚动；取消导航sticky。只改变设置页，其他页面滚动不变。
- organizeSettingsNavigation将settings-grid、插件、关于装入settings-content，保留现有ID、分组保存与脏状态。点击分区通过容器scrollTo定位，heading焦点preventScroll，避免外层页面被scrollIntoView带走；删除固定70px scrollMarginTop。导航窄窗可横向滚动，分隔线/背景/字号沿用主题令牌，首个模型按钮初始选中。
- 新tests/settings_navigation_ui.mjs先在旧布局因缺独立内容区失败，再验证1440/1040/800宽度的所有8分区跳转、标题导航位置、内容可见无重叠、外层scrollTop为0、手动滚动和未保存输入保留，全部通过且无页面错误。截图pw-debug/settings-navigation/settings-*.png，checks.json。新手指引测试改检查settings-content真实滚动；导览8组、workspace_ui完整交互回归通过，271项Node通过。
- 版本同步2.7.67，构建及verify_release全PASS（59web资源/65后端模块及安装payload/摘要），日志pw-debug/settings-navigation/{onboarding-ui,workspace-ui,2.7.67-node-tests,2.7.67-build,2.7.67-release-verify}.log。没有安装或改用户真实数据。
- release/NPL-2.7.67-Setup.exe：60850751字节，SHA256 61213e21a9ae8a4ccdc94abd9b452c63de27a3458a4e1fb147362cb37af0fa3f。
- release/NPL-2.7.67-win-x64-portable.zip：49618762字节，SHA256 7bfed9ab774426105a2389720e45a84e4fabc4a4855530f8511ccd1edaeea2c5。
- release/NPL-2.7.67-Connector.zip：49719字节，SHA256 73e2b89b8ab629fa1a51cd2146d2e6fcc335616a007443d9b675828cf984f57d。

### 2026-11-02 NPL 2.7.68：EndNote / Zotero 笔记迁移与展示（已打包，未安装）

- 用户问旧文献库笔记是否可见，检查发现旧导入器只收元数据/PDF，EndNote Notes/Research Notes未读取，RDF Memo子笔记被跳过，程序本身papers.notes也缺少展示入口；用户明确要求补强。
- 新services/imported_notes.py将来源笔记独立存到papers.imported_notes JSON列，由db._migrate给旧库添加默认[]；db.paper_to_dict移除此字段，避免每个文献列表项/AI上下文携带迁移正文。GET /api/papers/{id}/notes按需返回personal与imported。原papers.notes保留个人笔记/AI回答追加行为，不覆盖或拼进迁移内容。
- importers读取EndNote XML notes/research-notes（包括style face文字格式与段落）、Zotero RDF文献→isReferencedBy→Memo（支持无itemType的官方Memo）、文献→link:link→附件备注、CSV Notes/Note/笔记/Research Notes、RIS N1及续行、BibTeX note与annote/annotation原文。支持rdf:resource附件路径与nodeID，HTML转安全Markdown，保留基本文字/段落/标题/强调/列表/网页链接。脚本不执行，图片明示未迁移。摘要、CSV Extra不混成笔记；CSV独立note/annotation/attachment行不变成论文。
- 来源关联参照Zotero官方RDF源码 https://github.com/zotero/translators/blob/master/Zotero%20RDF.js ：官方子Memo常没有itemType，正文在rdf:value，通过dcterms:isReferencedBy挂父文献。孤立笔记不猜归属，结果有警告。未匹配真实PDF仍禁止新建Zotero条目；已存在论文可不带PDF补导笔记。
- 笔记merge用BEGIN IMMEDIATE串行读取合并，以规范化内容SHA256跨重复导出/格式去重；源内容改变时保留为另一条内容，不覆盖旧笔记。单条>100万字符明确报错，不静默截断。merge失败会保留已创建文献并报告笔记错误，不把整条新增文献计数吞掉。结果含notes_imported/notes_duplicates/notes_updated_papers及warnings；只补入笔记时也显示成功并刷新界面。
- 新web/js/paper-notes.js及css：文献详情摘要下方“论文笔记”，阅读器新增独立“笔记”标签，原“批注”不变。分开呈现个人笔记和可折叠来源笔记；原导入内容只读，支持安全追加个人笔记。GET有AbortController/代次/宿主校验防跨篇迟到结果；草稿本次运行按论文暂存，追加期间新输入保留；加载失败明确提示、可刷新。AI回答存笔记后emit notes-changed刷新可见宿主。原阅读器End键导航测试期望改为新增最后页paper-notes。
- recovery._copy_rows只针对papers允许旧备份缺imported_notes列，保留其他严格schema检查；备份、回收站论文快照本就按数据库原行保存，因此包含新列。新旧备份实际restore roundtrip、删除恢复、并发导入均经测试。随包docs/笔记迁移说明.md说明导出步骤与边界；推荐Zotero RDF勾文件和笔记。PDF原生高亮坐标/笔记图片未迁移；要带Zotero数据库批注文字，请先对论文“从批注添加笔记”再导出 https://www.zotero.org/support/pdf_reader#adding_annotations_to_notes 。不声称复制PDF即可带出数据库批注。
- 验证：新增test_imported_notes.py 10项由红到绿（包含新PDF条目/已有无PDF补导、来源格式、HTML、保留个人笔记、重复/变更/并发、新旧真实备份与回收站）；所有*import*共51项Python通过，workspace_hardening18项通过；271项Node通过；imported_notes_ui实际XML/RDF上传补导→双入口→追加/跨篇草稿/安全HTML/失败重试5组通过；7页+阅读器branding、新手导览8组、reader_first_batch原生链接/目录/批注等回归通过。截图pw-debug/imported-notes/；日志import-tests.log、notes-ui.log、brand-ui.log、onboarding-ui.log、annotations-ui.log、2.7.68-node-tests.log。
- 测试修正：EndNote API旧测试会启动后台PDF下载并越过fixture生命周期，已mock其pdf_fetch_manager.start，后续完整51项无后台线程错误。最初新备份测试sqlite连接未关闭导致临时目录清理错误，改用closing后通过。最初笔记UI一次出现既有PDF切换Transport destroyed页面错误，记录stack后多次完整重跑及reader_first_batch均未复现；未扩大范围重写PDF渲染器。
- 构建与verify_release全PASS（61web资源/66后端模块、新说明、安装payload/版本与全部摘要一致）；日志pw-debug/imported-notes/2.7.68-{build,release-verify}.log。未安装或对真实用户文献执行迁移；旧遗漏笔记需用户从原库重新导出补导。
- release/NPL-2.7.68-Setup.exe：60868930字节，SHA256 17d978a0ef836bd6944a42c5e5cffad5df414ec2883b30242096e4cc4bdaf921。
- release/NPL-2.7.68-win-x64-portable.zip：49637138字节，SHA256 97d7426ea0e351abcafd4d0fbfbcdde877c9f4d925ff1a8b51152ab80ac30f86。
- release/NPL-2.7.68-Connector.zip：49720字节，SHA256 4f9b6341ce053f7f70a36f14d057bca676d86fed4482d4f061dff8737fe86e64。
