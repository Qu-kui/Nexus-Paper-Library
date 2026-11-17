# Nexus Paper Library（NPL）

2026-11-02，用户确认论文管理器的正式名称为 **Nexus Paper Library**，简称 **NPL**，与课题组 Nonlinear Photonics Lab 的缩写呼应。

- Nexus：连接、汇聚，表达跨论文的知识关联。
- Paper：论文。
- Library：文献库。

视觉方向：延续课题组紫色和几何折面，以清晰展开的书页为主体；将 N、P、L 的笔画融入左页、右页、书脊和底边，而非另贴字母。

用户最终选定紫色左书页内的留白 N 微调方案：保留左页实体和负形 N，略增强 N 的辨识；顶部两条弧形书页适度向外延长，同时留在主体两侧边界内。右侧 P 和延长后与右端面对齐的 L 底边保持原形。当前选定图形见 [设计展示](../assets/branding/npl-negative-n-refined/npl-brand-board.png) 和 [编辑记录](../assets/branding/npl-negative-n-refined/design-notes.md)。后续落地以此方案为准，完整正形 N 等前几版仅作探索存档。

品牌已在 2.7.56 落地：界面、桌面和托盘、安装器、浏览器 Connector、Word 功能区与随包说明使用 Nexus Paper Library（NPL）。移除鲸形图案与旧品牌自称。

生产品牌色为 `#6e0342`（RGB 110, 3, 66），匹配用户最终选定参考图的实色区域。

生产图形位于 `web/brand/npl-mark.svg`（紫色独立标志）、`web/brand/npl-app.svg`（紫色底反白图标）；保留选定的留白 N、略加长的顶部书页和右侧 L 底边对齐。独立标志中 N 为白色留白；反白图标中 N 为紫色负形。顶部书页端点为 x=222 / 672，均在主体 x=197…701 范围内，书脊汇聚点保持 (443, 256)。五条路径顺序固定为左顶部书页、右顶部书页、左上页、左下页、右侧 PL，并以 `data-part` 标记供动画复用。两份 SVG 是应用资源源文件，PNG、ICO 和浏览器多尺寸图标由这些资源生成。

2.7.59 启动动画先由三条细光线环绕，再沿同一套生产路径勾勒、填色，落定为上述静态标志，再沿书脊左右打开，露出真实主界面。仅新启动进程时播放，支持跳过和系统减少动画设置；详见 [启动动画说明](startup-animation.md)。

更新 SVG 后运行 `node tools/render_brand.mjs`，再运行 `.venv/Scripts/python.exe make_icon.py`。安装包内只包含生产资源，不包含概念展示板。

历史项目名为 PaperWhale。为了兼容升级，数据路径、数据库、内部程序文件名、模块/API、旧 Connector 发现标识、Word 模板文件名和偏好键保留。外部发布名称为 `NPL-<版本>-Setup.exe` / `NPL-<版本>-win-x64-portable.zip` / `NPL-<版本>-Connector.zip`。
