# 内置科研 Skills 来源与许可

`skills/` 目录随 PaperWhale 一起分发，供程序内「科研技能」页使用。
以下为上游客源仓库与许可，完整许可文本在 `skills/licenses/`。

| Skill | 上游仓库 | 许可 |
|---|---|---|
| academic-discovery | PaperWhale 原创应用检索规范 | MIT（项目根 LICENSE） |
| nature-reader / nature-paper-card / nature-writing / nature-polishing / nature-citation / nature-reviewer / nature-statistics / nature-response | https://github.com/yuan1z0825/nature-skills | Apache-2.0 |
| deeppapernote | https://github.com/917Dhj/DeepPaperNote | MIT |
| systematic-literature-review | https://github.com/bytedance/deer-flow | MIT |
| pyzotero | https://github.com/K-Dense-AI/scientific-agent-skills | MIT |
| knowledge-ops | https://github.com/affaan-m/ecc | MIT |
| memory-management | https://github.com/anthropics/knowledge-work-plugins | Apache-2.0 |

说明：

- `bib-search-citation` 因上游仓库未声明 license，未打包进分发包，仅保留在本机 Agent 技能目录。
- 这些 Skill 由各自作者维护；PaperWhale 只把 SKILL.md 及同目录引用文档作为提示词上下文使用，
  不执行其中的脚本（`scripts/` 仅随包保留，供高级用户参考）。
- 若要二次分发 PaperWhale，请保留本文件与 `skills/licenses/`。
