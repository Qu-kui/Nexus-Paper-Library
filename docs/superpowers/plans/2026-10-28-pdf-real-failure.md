# ScienceDirect 实际失败修正（2.5.2）

**Evidence:** 用户实际加载主程序及 Edge 未打包 Connector 均为 2.5.1。目标 DOI 10.1016/j.optlaseng.2020.106055，数据库 be8cd7d6d82845f3ae7d898c64c42f96 的逐路线记录显示 Elsevier linkinghub HTML 刷新未被跟进，/pdf 返回403；用户确认入口是浏览器插件。此前只按 cached manifest 名称识别插件漏查未打包目录，已核实真实 path manifest。

**Goal:** 修复已复现跳转遗漏，保留插件真实下载失败证据，避免用后台补抓错误掩盖浏览器失败。不能以模拟测试声称该真实论文全文下载成功。

- [x] 先写 linkinghub refresh、完整 DOI→中转→文章→PDF、跳转深度及循环/凭据隔离回归，复现旧失败。
- [x] 已知 Elsevier 中转允许声明的 HTML refresh，刷新按 HTTP 导航处理，不耗文章展开深度；16 次请求、循环检测与总时限继续有效。公开地址实测已到 ScienceDirect，仍HTTP403。
- [x] Connector 区分 HTTP、超时、网络/跨域、HTML响应、大小/校验失败；保留脱敏逐步记录并显示弹窗原因。
- [x] 后端保存 browser_attempts，后台任务并发更新不覆盖；文献详情显示浏览器尝试与后台记录。
- [x] 完整回归及审查，更新 2.5.2 包和 Connector，发布验证。真实 Edge 自动化曾因 URL 识别策略阻断，不以其他自动化方式绕过该限制。

尚未闭环：目标论文的真实插件全文获取仍需新版在用户授权浏览器中复现，读取脱敏browser_attempts后才能确定浏览器失败原因；不能将本计划交付等同该论文已成功下载。
