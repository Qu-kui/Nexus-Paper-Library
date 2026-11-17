# Referer 探针（独立最小实验）

> ## 结论（2026-10-28 自动化实测，此路不通，别再重复做）
>
> 用 headless Edge + 一次性 MV3 扩展实测：`chrome.downloads.download({url, headers:[{name:'Referer', value:...}]})`
> **直接抛 `Unsafe request header name`**——Referer 是 Chromium 的禁止设置请求头，不是"被静默丢弃"，
> 而是 API 层直接拒绝。所以「用扩展下载 API 伪造 Referer 拿出版社签名 PDF」这条路不存在。
>
> 对照实验（不带 headers）下载被接受，但 headless 下 state=interrupted / error=USER_CANCELED，
> 那只说明无头环境取消了下载，与 Referer 无关。
>
> 附带推论：页面内同源 fetch 本来就会自动带 Referer，SD 仍回 HTML，说明 SD 区分的是
> 「导航请求 vs 普通请求」（Sec-Fetch-Mode 之类），不是 Referer。伪造 Referer 方向整体作废。
>
> `tests/referer_probe/` 保留，以后验证下载行为（Content-Disposition、文件名、headless 下载状态）还能用。

**要回答的唯一问题**：Edge/Chrome 的 MV3 `chrome.downloads.download({ url, headers: [{name:'Referer', value:...}] })`
会不会真的把 Referer 发出去？这决定 PaperWhale 能不能用「扩展下载 API + Referer」直接拿出版社签名 PDF，
而不是去点 `<a download>`（跨源失效、没有 userGesture、失败会落 init.htm）。

**边界**：只监听 `127.0.0.1:8799`，只回一个 1KB 的假 PDF；扩展只有 `downloads` 权限；
不接触 PaperWhale 数据库、不读真实 Cookie（服务端只记“有没有 Cookie”，不记内容）、不访问外网。
生产 `extension/` 一行都不改。

## 步骤（需要用户手动点一次，2 分钟）

1. 起探针服务（在项目根目录，PowerShell）：
   ```
   .venv\Scripts\python.exe tests\referer_probe\server.py
   ```
   看到 `PW Referer Probe listening on http://127.0.0.1:8799` 就绪。先别关这个窗口。

2. 装实验扩展（不碰正式 Connector）：
   - Edge 打开 `edge://extensions` → 打开「开发人员模式」→「加载解压缩的扩展」→
     选 `D:\Harness APP\PaperWhale\tests\referer_probe\extension`
   - 建议先把正式 PaperWhale Connector 临时关掉，避免混淆（可选）。

3. 点扩展图标 → 弹窗里点「开始实验」。等它出现「全部完成」。

4. 收集三样东西：
   - 弹窗里 `<pre>` 的全文（实验 1/2 的 `state/filename/mime/bytes`）；
   - 服务端窗口最后 2 行 JSON（每行一个请求，含 `referer` 字段）；
   - 如果实验 1 直接报「downloads.download 直接拒绝」，把那段报错原文保存下来。

5. 点弹窗里的「清理实验痕迹」，然后 Ctrl+C 关服务、在 `edge://extensions` 里移除实验扩展。

## 判定规则（拿到结果后不用再猜）

| 观察 | 结论 |
| --- | --- |
| 实验 1 服务端日志 `referer = http://127.0.0.1:8799/page`，且文件是 PDF | **Referer 被放行**：生产可以加一条 `downloads.download({headers:[{name:'Referer',value:文章页URL}]})`，失败再回退现有流程 |
| 实验 1 日志 `referer` 为空、实验 2 也为空 | Referer 被 Chromium 静默丢弃：不做这条自动路线，保留「用户手动点 Download PDF + 接住下载」 |
| 实验 1 `downloads.download` 直接抛错/返回 undefined | 当前浏览器不支持该参数：同上，不做 |
| 实验 1 有 Referer 但下载到的不是 PDF | 还要再测出版社实际行为，不能据此上线 |

## 已知不确定点（为什么必须实测）

Chrome 官方文档对 `DownloadOptions.headers` 说明不完整，且 Referer 属于受限请求头；
Chromium 可能在下载管线里静默丢弃它。今天查文档没有拿到可引用的结论（Chrome 文档页抓取被截断/404），
所以这里不下断言，用一次真机实验定性。

## 文件

- `server.py`：探针服务（`/page` 测试页、`/signed.pdf` 回 `Content-Disposition: attachment` 的 PDF，逐行打 JSON 日志到 `requests.log`）
- `extension/`：临时 MV3 扩展（只有 downloads 权限；两个 probe + 清理按钮）
- `requests.log`：服务端追加日志（实验后可删）
