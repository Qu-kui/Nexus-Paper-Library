# NPL Connector（浏览器插件）

在网页上点一下，把文献元数据保存到 NPL，能抓到 PDF 就一起抓；抓不到就按网页信息入库。

## 安装（Microsoft Edge / Google Chrome，免商店）

### Edge（推荐，Windows 自带）
1. 先启动 NPL。
2. 双击扩展目录里的 `安装到Edge.cmd`，或手动打开 `edge://extensions`。
3. 打开「开发人员模式」→「加载解压缩的扩展」→ 选择本文件夹。
4. 打开论文页面，点工具栏 NPL 保存。

### Chrome


1. 先启动 NPL。
2. 打开 `chrome://extensions`。
3. 打开「开发者模式」→「加载已解压的扩展程序」→ 选择本文件夹。

## 功能

- 用浏览器自己的登录态抓 PDF（机构订阅/出版社页面），比后端直连成功率高
- 并行探测本机端口，弹窗打开更快

- 自动识别 Highwire / Dublin Core / Open Graph / JSON-LD 元数据，覆盖 arXiv、PubMed、IEEE、ACM、Springer、Nature 等常见站点
- 抓取 `citation_pdf_url`、arXiv PDF 链接、页面内嵌 PDF
- 抓不到 PDF 时保存标题、作者、年份、期刊、DOI、摘要，后台再按 DOI/arXiv 尝试补 PDF
- 保存前可以手动改字段
- 支持「入库后自动精读」（需要 PDF）

## 端口

NPL 默认监听 `8765`，被占用时用 8766~8794，插件会自动探测。多个实例并存时可在插件设置里固定端口。

## 权限说明

- `activeTab`：只在你点开插件时读取当前页面
- `scripting`：提取当前页面元数据
- `host_permissions` 仅 `127.0.0.1` / `localhost`：只和本机 NPL 通信，不上传任何服务器
