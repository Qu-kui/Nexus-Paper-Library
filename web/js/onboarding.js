/* Guided tours use real controls, but never submit forms or start AI jobs. */
const KEY = 'npl.onboarding.v1';
const READER_KEY = 'npl.onboarding.reader.v1';
const $ = selector => document.querySelector(selector);
const card = selector => () => $(selector)?.closest('.card');
const mainSteps = [
  {id:'welcome', page:'library', target:'.sidebar nav', title:'欢迎来到 NPL', text:'接下来带你走一遍真实工作台。左边是各个功能入口，点击「下一步」会自动切页并框出对应功能。随时可以跳过，之后从帮助菜单或设置里重新打开。'},
  {id:'model', page:'settings', target:card('#set-base-url'), title:'先连接你的大模型', text:'填入服务商的 Base URL、API Key 和模型名，保存设置后点击顶部「测试连接」。本地模型也可以使用兼容接口；暂时不配置，仍可导入、管理和阅读 PDF。'},
  {id:'profile', page:'settings', target:card('#set-profile'), title:'让 AI 了解你的研究背景', text:'写下研究方向、当前课题和关注的问题，并点击「保存画像」。关联分析、论文发现等功能会按各自规则引用这些背景；阅读对话不会被自动保存，关闭阅读器时可选择存到这篇论文。'},
  {id:'files', page:'import', target:'#dropzone', title:'把第一篇论文放进来', text:'将 PDF 拖入这里，或点击「选择 PDF」。同页也支持文件夹扫描、Zotero、EndNote 等文献库迁移。导览结束后再操作即可。'},
  {id:'links', page:'import', target:card('#identifier-input'), title:'只有链接，也能添加', text:'粘贴 DOI、arXiv 编号或论文网址，程序会获取元数据并尝试下载 PDF。浏览器插件还能从正在浏览的论文网页收藏文献；受权限限制的全文可能需要登录出版商。'},
  {id:'library', page:'library', target:'.page[data-page="library"] .toolbar', title:'在文献库中快速定位', text:'按标题、作者、摘要或标签搜索，再用分类、标签和排序缩小范围。标题默认显示两行；列表里的列宽可以自行拖动调整。'},
  {id:'reading', page:'library', target:'.library-main', title:'从这里进入论文阅读', text:'单击条目查看详情，双击打开阅读器。阅读器支持全文搜索、批注、精读和本篇问答；第一次打开论文时还会有一段专门的阅读指引。没有论文时，先从「导入 / 添加」开始。'},
  {id:'discover', page:'discover', target:card('#discover-input'), title:'用自然语言寻找论文', text:'告诉 AI 你想了解哪个领域的新进展，或希望梳理什么发展脉络。搜索得到的候选论文由你决定加入或忽略；本页上方另有 Google Scholar 邮件提醒。'},
  {id:'chat', page:'chat', target:'.kb-files', fallback:'#chat-input', title:'跨论文问答，先选资料', text:'在文件列表选择本轮要参考的论文，再输入问题。这里用于多篇文献的综合分析；只讨论某一篇时，使用阅读器里的本篇问答，更容易保持上下文清晰。'},
  {id:'methods', page:'methods', target:'.page[data-page="methods"] .page-head', title:'把方法和参数集中整理', text:'这里集中查看从论文提取的方法与参数，并可搜索比较。提取会调用大模型，点击相应按钮后才会开始；批量任务的进度和失败重试可在任务中心查看。'},
  {id:'terms', page:'terms', target:'.page[data-page="terms"] .page-head', title:'把论文里的术语和缩写背下来', text:'选「全部英文文献」后点「提取缩写」，或点「AI 抽取」逐篇挑出术语和缩写（带中文释义、音标、词性）。复习提供 2–6 个选项，两个方向分别排期。先回想，再选择；不确定可点「模糊」。双向满分为练习熟识，7 天后仍会维护复习。可搜索全部词条并编辑纠错。'},
  {id:'plugins', page:'settings', target:card('#btn-open-extension'), title:'连接浏览器和 Word', text:'这里可以打开浏览器扩展文件夹，或安装 Word 引用插件。浏览器扩展仍需在浏览器中加载；Word 安装后可在写作时插入文献引用。'},
  {id:'finish', page:'library', target:'#btn-quick-upload', title:'现在，开始整理你的论文', text:'点击「完成指引」回到文献库，就可以添加第一篇论文了。遇到问题时，在帮助菜单查看使用说明；也能从「设置 → 关于」重新开始这段指引。'},
];
const readerSteps = [
  {id:'reader-tools', target:'.reader-tools', title:'阅读器：查找与导航', text:'顶部可以翻页、输入页码跳转、全文搜索；右侧面板可收起，留出更大阅读空间。关闭阅读器会回到工作台，并询问这次对话要不要保存。'},
  {id:'reader-annotation', target:'#pdf-scroll', title:'边读边标注', text:'在 PDF 正文中拖动选择文字，即可使用浮动工具高亮、做笔记或就选中内容提问。批注与阅读位置会随这篇论文保存。'},
  {id:'reader-ai', target:'#reader-sidebar-toggle', title:'精读与阅读对话', text:'「展开侧栏」后可以切换精读、论文对话、对话地图等面板。一键精读整理核心内容；这轮阅读的对话是全篇共用的，切换 PDF 或共读论文都不会换对话，关闭阅读器时再决定保不保存。'},
  {id:'reader-co', target:'#btn-co-add', title:'多篇共读与左右对比', text:'把最多 4 篇论文放进同一组，它们共用同一个阅读对话；点「左右对比」可以并排看两篇，右栏和主栏一样能划词翻译、追问和批注。'},
  {id:'reader-done', target:'#reader-tabs', title:'随时可切换面板', text:'右侧可切换 AI 精读、论文对话、对话地图、目录、批注和笔记；从对话里选中文字可以直接新建分支提问。点「完成指引」开始阅读。'},
];

function stored(key) { try { return localStorage.getItem(key); } catch { return 'unavailable'; } }
function save(key, value) { try { localStorage.setItem(key, value); } catch { /* Tour remains usable without storage. */ } }
function readerVisible() { return $('#reader') && !$('#reader').classList.contains('hidden'); }
function blocked() {
  return window.__NPL_STARTUP__?.requested || [...document.querySelectorAll('.modal:not(.hidden),#pw-guide-overlay')].some(el => !el.hidden && el.getClientRects().length);
}

export function initOnboarding({goto}) {
  let tour = null, frame = 0, pendingAuto = 0;
  const motion = matchMedia('(prefers-reduced-motion: reduce)');
  function position() {
    frame = 0;
    if (!tour) return;
    if (tour.kind === 'reader' && !readerVisible()) { end('interrupted'); return; }
    const {root, target, panel, spot} = tour;
    const top = document.body.classList.contains('desktop-shell') ? 42 : 8;
    const width = document.documentElement.clientWidth, height = innerHeight;
    const rect = target?.isConnected && target.getClientRects().length ? target.getBoundingClientRect() : null;
    const left = rect ? Math.max(8, rect.left - 6) : Math.max(8, width / 2 - 80);
    const right = rect ? Math.min(width - 8, rect.right + 6) : left + 160;
    const upper = rect ? Math.max(top, rect.top - 6) : top + 20;
    const lower = rect ? Math.min(height - 8, rect.bottom + 6) : upper + 40;
    spot.style.cssText = `left:${left}px;top:${upper}px;width:${Math.max(0,right-left)}px;height:${Math.max(0,lower-upper)}px`;
    const pw = panel.offsetWidth, ph = panel.offsetHeight, gap = 18;
    const roomRight = width - right, roomLeft = left;
    let x, y;
    if (roomRight >= pw + gap + 8) { x = right + gap; y = upper; }
    else if (roomLeft >= pw + gap + 8) { x = left - pw - gap; y = upper; }
    else if (height - lower >= ph + gap + 8) { x = left; y = lower + gap; }
    else if (upper - top >= ph + gap) { x = left; y = upper - ph - gap; }
    else { x = width - pw - 16; y = height - ph - 16; }
    panel.style.left = `${Math.max(8,Math.min(x,width-pw-8))}px`;
    panel.style.top = `${Math.max(top,Math.min(y,height-ph-8))}px`;
    root.dataset.target = target?.id || tour.steps[tour.index].id;
  }
  function schedulePosition() { if (tour && !frame) frame = requestAnimationFrame(position); }
  function resized() {
    tour?.target?.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});
    schedulePosition();
  }
  function show(index) {
    if (!tour) return;
    tour.index = index;
    const step = tour.steps[index];
    if (step.page) { tour.navigating = true; goto(step.page); tour.navigating = false; }
    let target = typeof step.target === 'function' ? step.target() : $(step.target);
    if (!target?.getClientRects().length) target = $(step.fallback || (tour.kind === 'reader' ? '#reader-title' : '.page.active .page-head'));
    tour.target = target;
    target?.scrollIntoView({block:'center', inline:'nearest', behavior:'instant'});
    tour.root.dataset.step = step.id;
    tour.root.querySelector('.tour-count').textContent = `${tour.kind === 'reader' ? '阅读指引' : '新手指引'} · ${index+1} / ${tour.steps.length}`;
    tour.root.querySelector('h2').textContent = step.title;
    tour.root.querySelector('.tour-copy').textContent = step.text;
    tour.root.querySelector('.tour-progress-fill').style.transform = `scaleX(${(index+1)/tour.steps.length})`;
    const previous = tour.root.querySelector('[data-tour="previous"]');
    previous.disabled = index === 0;
    const next = tour.root.querySelector('[data-tour="next"]');
    next.textContent = index === tour.steps.length-1 ? '完成指引' : '下一步 →';
    position();
    tour.animation?.cancel();
    if (!motion.matches) tour.animation = tour.panel.animate([{opacity:0,transform:'translateY(6px)'},{opacity:1,transform:'translateY(0)'}],{duration:180,easing:getComputedStyle(document.documentElement).getPropertyValue('--ease-decelerate').trim()});
    next.focus({preventScroll:true});
  }
  function end(status) {
    if (!tour) return;
    const previous = tour;
    tour = null;
    previous.animation?.cancel();
    previous.observer.disconnect();
    previous.readerObserver.disconnect();
    previous.root.remove();
    cancelAnimationFrame(frame); frame = 0;
    window.removeEventListener('resize', resized);
    document.removeEventListener('scroll', schedulePosition, true);
    document.removeEventListener('keydown', keydown, true);
    document.removeEventListener('focusin', guardFocus, true);
    save(previous.kind === 'reader' ? READER_KEY : KEY, status);
    if (previous.focus?.isConnected && previous.focus.getClientRects().length && !previous.focus.closest('[inert]')) previous.focus.focus({preventScroll:true});
    else (previous.kind === 'reader' ? $('#reader-close') : $('.nav-item.active'))?.focus({preventScroll:true});
  }
  function keydown(event) {
    if (!tour || event.target.closest('#app-titlebar')) return;
    if (event.key === 'Escape') { event.preventDefault(); event.stopImmediatePropagation(); end('skipped'); return; }
    if (event.key === 'Tab') {
      const buttons = [...tour.panel.querySelectorAll('button:not(:disabled)')];
      const index = buttons.indexOf(document.activeElement);
      event.preventDefault(); event.stopImmediatePropagation();
      buttons[(index + (event.shiftKey ? -1 : 1) + buttons.length) % buttons.length].focus();
    }
  }
  function guardFocus(event) {
    if (tour && !tour.root.contains(event.target) && !event.target.closest('#app-titlebar')) tour.root.querySelector('[data-tour="next"]').focus({preventScroll:true});
  }
  function start(kind = 'main') {
    clearTimeout(pendingAuto);
    if (blocked()) return;
    if (tour) end('interrupted');
    // A help-menu replay from the reader keeps that reader open and explains its controls.
    if (readerVisible()) kind = 'reader';
    if (kind === 'reader' && !readerVisible()) return;
    const root = document.createElement('section');
    root.id = 'npl-tour'; root.className = 'npl-tour';
    root.setAttribute('role','dialog'); root.setAttribute('aria-modal','true'); root.setAttribute('aria-labelledby','tour-title'); root.setAttribute('aria-describedby','tour-copy');
    root.innerHTML = `<div class="tour-spot" aria-hidden="true"></div><div class="tour-panel">
      <div class="tour-top"><span class="tour-count"></span><button type="button" class="btn tiny ghost" data-tour="skip">跳过</button></div>
      <div class="tour-progress" aria-hidden="true"><div class="tour-progress-fill"></div></div>
      <div aria-live="polite" aria-atomic="true"><h2 id="tour-title"></h2><p id="tour-copy" class="tour-copy"></p></div>
      <div class="tour-actions"><button type="button" class="btn" data-tour="previous">上一步</button><button type="button" class="btn primary" data-tour="next">下一步 →</button></div>
      <p class="tour-hint">Esc 可退出 · 导览不会自动提交或生成内容</p></div>`;
    document.body.appendChild(root);
    // A missing optional stylesheet must not leave an unstyled modal blocking the workspace.
    if (getComputedStyle(root).position !== 'fixed') { root.remove(); return; }
    tour = {kind, root, panel:root.querySelector('.tour-panel'), spot:root.querySelector('.tour-spot'), steps:kind === 'reader' ? readerSteps : mainSteps, focus:document.activeElement, index:0};
    save(kind === 'reader' ? READER_KEY : KEY, 'started');
    $('#setup-banner')?.classList.add('hidden');
    tour.observer = new ResizeObserver(schedulePosition);
    tour.observer.observe(tour.panel); tour.observer.observe(document.body);
    tour.readerObserver = new MutationObserver(schedulePosition);
    tour.readerObserver.observe($('#reader'), {attributes:true,attributeFilter:['class']});
    root.addEventListener('click', event => {
      const action = event.target.closest('[data-tour]')?.dataset.tour;
      if (!tour || !action) return;
      if (action === 'skip') end('skipped');
      if (action === 'previous' && tour.index > 0) show(tour.index-1);
      if (action === 'next') tour.index === tour.steps.length-1 ? end('completed') : show(tour.index+1);
    });
    document.addEventListener('keydown',keydown,true);
    document.addEventListener('focusin',guardFocus,true);
    document.addEventListener('scroll',schedulePosition,true);
    window.addEventListener('resize',resized);
    show(0);
  }
  $('#btn-onboarding')?.addEventListener('click',()=>start());

  document.addEventListener('pw:onboarding-start',()=>start());
  document.addEventListener('pw:onboarding-dismiss',()=>end('interrupted'));
  document.addEventListener('pw:page-changed',()=>{if(tour && !tour.navigating) end('interrupted');});
  document.addEventListener('pw:reader-opening',()=>{if(tour)end('interrupted');});
  document.addEventListener('pw:reader-opened',()=>{
    if (!tour && stored(KEY) === 'completed' && !stored(READER_KEY) && !blocked()) start('reader');
  });
  // Do not surprise existing users who already dismissed setup, or overlap the launch animation.
  if (!stored(KEY) && stored('pw-setup-dismissed') !== '1') {
    let attempts = 0;
    const attempt = () => {
      if (stored(KEY)) return;
      if ((blocked() || document.hidden) && ++attempts < 60) { pendingAuto = setTimeout(attempt,250); return; }
      if (!blocked() && !document.hidden && !readerVisible() && !location.hash) start();
    };
    pendingAuto = setTimeout(attempt,400);
  }
}
