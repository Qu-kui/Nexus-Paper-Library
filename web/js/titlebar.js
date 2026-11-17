/* 自定义窗口框：只在 pywebview 桌面窗口里显示（浏览器模式保持原样）。 */
(function () {
  const api = () => (window.pywebview && window.pywebview.api) || null;

  function nav(page) {
    const reader = document.getElementById('reader');
    if (reader && !reader.classList.contains('hidden')) document.getElementById('reader-close')?.click();
    document.querySelector(`.nav-item[data-page="${page}"]`)?.click();
  }

  async function showAbout() {
    let version = "";
    try {
      const response = await fetch("/api/health");
      const data = await response.json();
      version = data.version ? ` ${data.version}` : "";
    } catch {
      /* 离线时只显示名字 */
    }
    window.alert(`Nexus Paper Library${version}\n本地优先的 AI 文献工作台\n数据全部保存在本机。`);
  }

  function setupGuideToc(root) {
    const scroller = document.getElementById("pw-guide-body");
    const links = Array.from(root.querySelectorAll(".pw-guide-toc a"));
    const sections = links
      .map((link) => root.querySelector(link.getAttribute("href")))
      .filter(Boolean);
    if (!scroller || !links.length || !sections.length || !("IntersectionObserver" in window)) return;
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          const id = `#${entry.target.id}`;
          links.forEach((link) => link.classList.toggle("active", link.getAttribute("href") === id));
        });
      },
      { root: scroller, rootMargin: "-8% 0px -78% 0px", threshold: 0 }
    );
    sections.forEach((section) => observer.observe(section));
    links.forEach((link) => link.addEventListener("click", () => links.forEach((x) => x.classList.remove("active"))));
  }

  function ensureGuideStyles() {
    if (document.getElementById("pw-guide-style")) return;
    const link = document.createElement("link");
    link.id = "pw-guide-style";
    link.rel = "stylesheet";
    link.href = "/css/help.css";
    document.head.appendChild(link);
  }

  let guideLastFocus = null;

  function closeGuide() {
    const overlay = document.getElementById("pw-guide-overlay");
    if (overlay) overlay.hidden = true;
    document.removeEventListener("keydown", guideKeydown);
    if (guideLastFocus && document.contains(guideLastFocus)) guideLastFocus.focus();
    guideLastFocus = null;
  }

  function guideKeydown(event) {
    if (event.key === "Escape") { closeGuide(); return; }
    if (event.key !== "Tab") return;
    const overlay = document.getElementById("pw-guide-overlay");
    const sheet = overlay?.querySelector(".pw-guide-sheet");
    if (!sheet) return;
    const focusables = [...sheet.querySelectorAll('a[href], button:not([disabled]), input, select, textarea, [tabindex]:not([tabindex="-1"])')]
      .filter((node) => node.offsetParent !== null);
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }

  async function openGuide() {
    ensureGuideStyles();
    let overlay = document.getElementById("pw-guide-overlay");
    if (!overlay) {
      guideLastFocus = document.activeElement;
      overlay = document.createElement("div");
      overlay.id = "pw-guide-overlay";
      overlay.className = "pw-guide-overlay";
      overlay.setAttribute("aria-modal", "true");
      overlay.innerHTML = `
        <div class="pw-guide-sheet" role="dialog" aria-label="使用说明">
          <div class="pw-guide-toolbar">
            <span>使用说明 · Esc 或点「知道了」关闭</span>
            <div>
              <button type="button" data-guide-act="file">在文件夹中查看</button>
              <button type="button" class="primary" data-guide-act="close">知道了</button>
            </div>
          </div>
          <div class="pw-guide-scroll" id="pw-guide-body">
            <div style="padding:32px;color:#6b7c8f">正在加载说明…</div>
          </div>
        </div>`;
      overlay.addEventListener("click", (event) => {
        if (event.target === overlay) closeGuide();
      });
      overlay.querySelector('[data-guide-act="close"]').addEventListener("click", closeGuide);
      overlay.querySelector('[data-guide-act="file"]').addEventListener("click", () => {
        const bridge = api();
        if (bridge && typeof bridge.open_guide === "function") bridge.open_guide();
      });
      document.body.appendChild(overlay);
      const body = overlay.querySelector("#pw-guide-body");
      try {
        const response = await fetch("/js/guide-content.html", { cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        body.innerHTML = await response.text();
        setupGuideToc(body);
        overlay.querySelector('[data-guide-act="close"]').focus();
      } catch (error) {
        console.warn("guide load failed", error);
        body.innerHTML = '<div style="padding:32px;color:#6b7c8f">说明加载失败。可点右上角「在文件夹中查看」，或到程序目录打开 使用说明.txt。</div>';
      }
    }
    overlay.hidden = false;
    document.addEventListener("keydown", guideKeydown);
  }

  function closeMenus() {
    document.querySelectorAll(".tb-menu.open").forEach((node) => node.classList.remove("open"));
  }

  const MENUS = [
    {
      label: "文件",
      items: [
        { label: "导入 / 添加论文", run: () => nav("import") },
        { label: "为缺失条目补全 PDF", run: () => document.getElementById("btn-fetch-pdfs")?.click() },
        { sep: true },
        { label: "退出 NPL", run: () => api()?.window_quit?.() },
      ],
    },
    {
      label: "编辑",
      items: [
        { label: "刷新界面", run: () => window.location.reload() },
        { label: "最大化 / 还原", run: () => api()?.window_toggle_fullscreen?.() },
      ],
    },
    {
      label: "查看",
      items: [
        { label: "文献库", run: () => nav("library") },
        { label: "知识库问答", run: () => nav("chat") },
        { label: "发现论文", run: () => nav("discover") },
        { label: "设置", run: () => nav("settings") },
      ],
    },
    {
      label: "工具",
      items: [
        { label: "一键精读未读论文", run: () => document.getElementById("btn-analyze-all")?.click() },
        { label: "补全 PDF", run: () => document.getElementById("btn-fetch-pdfs")?.click() },
        { label: "设置 / 备份恢复", run: () => nav("settings") },
      ],
    },
    {
      label: "帮助",
      items: [
        { label: "新手指引", run: () => { closeGuide(); document.dispatchEvent(new CustomEvent('pw:onboarding-start')); } },
        { label: "使用说明", run: () => openGuide() },
        { label: "关于 NPL", run: showAbout },
      ],
    },
  ];

  function buildMenus() {
    const host = document.getElementById("tb-menus");
    if (!host) return;
    host.innerHTML = "";
    for (const menu of MENUS) {
      const wrap = document.createElement("div");
      wrap.className = "tb-menu";
      const button = document.createElement("button");
      button.type = "button";
      button.className = "tb-menu-btn";
      button.textContent = menu.label;
      const dropdown = document.createElement("div");
      dropdown.className = "tb-menu-list";
      for (const item of menu.items) {
        if (item.sep) {
          const sep = document.createElement("div");
          sep.className = "tb-menu-sep";
          dropdown.appendChild(sep);
          continue;
        }
        const entry = document.createElement("button");
        entry.type = "button";
        entry.className = "tb-menu-item";
        entry.textContent = item.label;
        entry.addEventListener("click", () => {
          closeMenus();
          document.dispatchEvent(new CustomEvent('pw:onboarding-dismiss'));
          item.run();
        });
        dropdown.appendChild(entry);
      }
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        const open = wrap.classList.contains("open");
        closeMenus();
        if (!open) wrap.classList.add("open");
      });
      wrap.appendChild(button);
      wrap.appendChild(dropdown);
      host.appendChild(wrap);
    }
  }

  function wireWindowButtons() {
    document.getElementById("tb-min")?.addEventListener("click", () => api()?.window_minimize?.());
    document.getElementById("tb-max")?.addEventListener("click", () => api()?.window_toggle_maximize?.());
    document.getElementById("tb-close")?.addEventListener("click", () => api()?.window_close?.());
    const dragArea = document.getElementById("tb-drag");
    dragArea?.addEventListener("dblclick", () => api()?.window_toggle_maximize?.());
  }

  /* 无边框窗口没有系统缩放边框：用八条透明手柄 + pywebview 的 move/resize 自己实现。 */
  const RESIZE_HANDLES = [
    ["n", "tb-resize-n"],
    ["s", "tb-resize-s"],
    ["e", "tb-resize-e"],
    ["w", "tb-resize-w"],
    ["ne", "tb-resize-ne"],
    ["nw", "tb-resize-nw"],
    ["se", "tb-resize-se"],
    ["sw", "tb-resize-sw"],
  ];
  const MIN_WIDTH = 1040;
  const MIN_HEIGHT = 700;

  async function beginResize(event, dir) {
    if (event.button !== 0) return;
    const bridge = api();
    if (!bridge || typeof bridge.window_geometry !== "function") return;
    event.preventDefault();
    event.stopPropagation();
    let geo;
    try {
      geo = await bridge.window_geometry();
    } catch {
      return;
    }
    // 整段拖动只认这一份「起点几何」。每帧都用 起点 + 累计位移 重算目标尺寸，
    // 绝不把起点改成上一帧的结果——那样位移会被反复叠加，窗口越拖越宽、完全不跟鼠标。
    const start = {
      x: Math.round(Number(geo.x) || 0),
      y: Math.round(Number(geo.y) || 0),
      w: Math.max(MIN_WIDTH, Math.round(Number(geo.width) || 1460)),
      h: Math.max(MIN_HEIGHT, Math.round(Number(geo.height) || 940)),
    };
    const startX = Number(event.screenX) || 0;
    const startY = Number(event.screenY) || 0;
    const pointerId = event.pointerId;
    const handle = event.currentTarget;
    try {
      handle?.setPointerCapture?.(pointerId);
    } catch {
      /* 捕获失败不影响后续逻辑，只是拖出窗口后可能收不到事件 */
    }
    let pending = null;
    let frame = 0;
    const applied = {...start};

    const apply = (next) => {
      // 西/北边先移动再缩放：pywebview 的 resize 固定左上角，先挪再加宽才不会跳。
      // 只发真正变化的那一项，鼠标没动就不重复调桥接。
      if (next.x !== applied.x || next.y !== applied.y) {
        applied.x = next.x;
        applied.y = next.y;
        bridge.window_move?.(next.x, next.y);
      }
      if (next.w !== applied.w || next.h !== applied.h) {
        applied.w = next.w;
        applied.h = next.h;
        bridge.window_resize?.(next.w, next.h);
      }
    };
    const flush = () => {
      frame = 0;
      const next = pending;
      pending = null;
      if (next) apply(next);
    };
    const onMove = (moveEvent) => {
      const dx = (Number(moveEvent.screenX) || 0) - startX;
      const dy = (Number(moveEvent.screenY) || 0) - startY;
      let nx = start.x;
      let ny = start.y;
      let nw = start.w;
      let nh = start.h;
      if (dir.includes("e")) nw = Math.max(MIN_WIDTH, start.w + dx);
      if (dir.includes("s")) nh = Math.max(MIN_HEIGHT, start.h + dy);
      if (dir.includes("w")) {
        const used = Math.min(dx, start.w - MIN_WIDTH);
        nw = start.w - used;
        nx = start.x + used;
      }
      if (dir.includes("n")) {
        const used = Math.min(dy, start.h - MIN_HEIGHT);
        nh = start.h - used;
        ny = start.y + used;
      }
      pending = { x: Math.round(nx), y: Math.round(ny), w: Math.round(nw), h: Math.round(nh) };
      if (!frame) frame = window.requestAnimationFrame(flush);
    };
    const finish = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", finish);
      window.removeEventListener("pointercancel", finish);
      try {
        handle?.releasePointerCapture?.(pointerId);
      } catch {
        /* 忽略 */
      }
      if (frame) {
        window.cancelAnimationFrame(frame);
        frame = 0;
      }
      // 松手时把最后一帧补上，避免刚好被取消的那一帧丢掉
      const last = pending;
      pending = null;
      if (last) apply(last);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", finish);
    window.addEventListener("pointercancel", finish);
  }

  function setupResizeHandles() {
    for (const [direction, className] of RESIZE_HANDLES) {
      const handle = document.createElement("div");
      handle.className = `tb-resize ${className}`;
      handle.addEventListener("pointerdown", (event) => beginResize(event, direction));
      document.body.appendChild(handle);
    }
  }

  function activate() {
    if (document.body.classList.contains("desktop-shell")) return;
    document.body.classList.add("desktop-shell");
    const bar = document.getElementById("app-titlebar");
    if (bar) bar.classList.remove("hidden");
    buildMenus();
    wireWindowButtons();
    setupResizeHandles();
  }

  document.addEventListener("click", closeMenus);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeMenus();
  });

  if (window.pywebview && window.pywebview.api) activate();
  window.addEventListener("pywebviewready", activate);

  // 支持 /#help 直接打开使用说明（也方便截图/自动化检查）
  if (location.hash === "#help") openGuide();
})();
