# PaperWhale 第三方组件与许可

PaperWhale 本体以 MIT 许可发布（见 `LICENSE`）。打包产物包含以下第三方组件，
它们各自的许可同样适用于对应部分。完整许可文本见 `licenses/` 目录。

## 运行时组件

| 组件 | 用途 | 许可 |
|---|---|---|
| Python 3.12 | 运行时 | PSF-2.0 |
| FastAPI | HTTP API | MIT |
| Starlette | ASGI 框架 | BSD-3-Clause |
| Uvicorn | ASGI 服务器 | BSD-3-Clause |
| Pydantic / pydantic-core | 数据校验 | MIT |
| HTTPX | HTTP 客户端 | BSD-3-Clause |
| python-multipart | 表单上传 | Apache-2.0 |
| bibtexparser | BibTeX 解析 | MIT |
| pypdfium2 + PDFium | PDF 文本抽取 | Apache-2.0 OR BSD-3-Clause（PDFium 及其依赖见 `licenses/`） |
| pywebview | 原生窗口（WebView2） | BSD-3-Clause |
| pythonnet / clr-loader | .NET 互操作 | MIT |
| PDF.js | PDF 渲染 / 文本层 | Apache-2.0 |
| PyInstaller bootloader | 可执行文件引导 | GPL-2.0 with bootloader exception |

## 说明

- 选择 pypdfium2 而不是 PyMuPDF，是为了避免 AGPL 传染，方便你把 PaperWhale
  作为闭源或开源软件自由分发。
- 模型 API 由用户自行配置，PaperWhale 不内置、不代理任何模型服务。
- 若你二次分发本软件，请保留 `LICENSE`、`THIRD-PARTY-NOTICES.md` 与 `licenses/`。
## 内置科研 Skills（skills/）

`academic-discovery` 是 PaperWhale 原创的应用内检索规范，适用本项目 MIT 许可；由发现论文流程自动调用。

PaperWhale 安装包内置以下第三方 Agent Skills，仅作为提示词规范使用，不执行其中脚本：

- nature-reader / nature-paper-card / nature-writing / nature-polishing / nature-citation / nature-reviewer / nature-statistics / nature-response
  来源：https://github.com/yuan1z0825/nature-skills ，Apache-2.0
- deeppapernote：https://github.com/917Dhj/DeepPaperNote ，MIT
- systematic-literature-review：https://github.com/bytedance/deer-flow ，MIT
- pyzotero：https://github.com/K-Dense-AI/scientific-agent-skills ，MIT
- knowledge-ops：https://github.com/affaan-m/ecc ，MIT
- memory-management：https://github.com/anthropics/knowledge-work-plugins ，Apache-2.0

完整许可文本见安装目录 `_internal/skills/licenses/`，来源与打包说明见 `_internal/skills/ATTRIBUTION.md`。
# KaTeX

KaTeX 0.19.1 is bundled for local mathematical typesetting under the MIT License.
Its license is included in web/vendor/katex/LICENSE and release/licenses/katex/LICENSE.
Project https://github.com/KaTeX/KaTeX

## Answer diagrams and charts

Apache ECharts 6.1.0 is bundled under Apache-2.0.
Its LICENSE and NOTICE are included in web/vendor/echarts/ and release/licenses/echarts/.
Project https://github.com/apache/echarts

Mermaid Tiny 12.3.0 is bundled under the MIT License.
Its license is included in web/vendor/mermaid/LICENSE and release/licenses/mermaid/LICENSE.
Project https://github.com/mermaid-js/mermaid
