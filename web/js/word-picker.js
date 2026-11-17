/* NPL · Word「添加引注」窗口
 * 数据来源（本机 NPL 服务）：
 *   GET /api/word/categories.txt   #stats + depth/path/count
 *   GET /api/word/papers.txt?q=&scope=&limit=500
 * 选择结果：
 *   POST /api/word/pick {token, ids}
 */
(function () {
  'use strict';

  var token = new URLSearchParams(location.search).get('token') || '';
  var state = {
    scope: '',
    q: '',
    total: 0,
    rows: [],          // {id, year, authors, title, venue, hasPdf}
    selected: new Set(),
    anchor: -1,
    count: 0,          // 当前分类总篇数
    sent: false
  };

  var $ = function (id) { return document.getElementById(id); };
  var rowsEl = $('rows');
  var treeEl = $('tree');
  var statusEl = $('status');
  var insertBtn = $('insert');
  var headCount = $('head-count');

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function flash(msg) {
    var el = document.createElement('div');
    el.className = 'wp-flash';
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(function () { el.remove(); }, 2600);
  }

  function setStatus(extra) {
    var parts = [];
    if (state.count) parts.push('共 ' + state.count + ' 篇');
    parts.push('已选 ' + state.selected.size + ' 篇');
    if (extra) parts.push(extra);
    statusEl.textContent = parts.join(' · ');
    headCount.textContent = state.count ? ('共 ' + state.count + ' 篇') : '';
    insertBtn.disabled = state.selected.size === 0;
  }

  /* ---------- 分类树 ---------- */

  function buildTree(rows) {
    var byPath = {};
    var roots = [];
    rows.forEach(function (r) {
      var node = { path: r.path, name: r.path.split(' / ').pop(), depth: r.depth, count: r.count, children: [], scope: r.path };
      byPath[r.path] = node;
      var parentPath = r.path.split(' / ').slice(0, -1).join(' / ');
      if (parentPath && byPath[parentPath]) byPath[parentPath].children.push(node);
      else roots.push(node);
    });
    return roots;
  }

  function renderTree(tree, stats) {
    treeEl.innerHTML = '';
    treeEl.appendChild(nodeEl({ name: '我的文库', count: stats.papers, scope: '', depth: 0, children: [], isRoot: true }));
    treeEl.appendChild(nodeEl({ name: '未分类', count: stats.uncategorized, scope: '__none__', depth: 0, children: [], isRoot: true }));

    function walk(nodes, host) {
      nodes.forEach(function (n) {
        host.appendChild(nodeEl(n));
        if (n.children.length) {
          var kids = document.createElement('div');
          kids.className = 'wp-kids';
          kids.hidden = !expanded.has(n.path);
          host.appendChild(kids);
          walk(n.children, kids);
        }
      });
    }
    var wrap = document.createElement('div');
    walk(tree, wrap);
    treeEl.appendChild(wrap);
    state.tree = tree;
    state.stats = stats;
  }

  var expanded = new Set();

  function nodeEl(n) {
    var el = document.createElement('div');
    el.className = 'wp-node' + (state.scope === n.scope ? ' active' : '');
    el.dataset.scope = n.scope;
    el.title = n.path || n.name;
    el.tabIndex = 0;
    el.setAttribute('role', 'treeitem');
    el.setAttribute('aria-selected', String(state.scope === n.scope));

    var twisty = document.createElement('span');
    twisty.className = 'twisty';
    var hasKids = n.children && n.children.length > 0;
    twisty.textContent = hasKids ? (expanded.has(n.path) ? '▾' : '▸') : '';
    if (hasKids) el.setAttribute('aria-expanded', String(expanded.has(n.path)));

    var name = document.createElement('span');
    name.className = 'name';
    name.textContent = n.name;

    var cnt = document.createElement('span');
    cnt.className = 'cnt';
    cnt.textContent = n.count ? String(n.count) : '';

    el.appendChild(twisty);
    el.appendChild(name);
    el.appendChild(cnt);

    el.addEventListener('click', function (ev) {
      if (hasKids && (ev.target === twisty)) {
        if (expanded.has(n.path)) expanded.delete(n.path); else expanded.add(n.path);
        renderTree(state.tree, state.stats);
        return;
      }
      state.scope = n.scope;
      state.selected.clear();
      document.querySelectorAll('.wp-node').forEach(function (x) {
        var on = x.dataset.scope === n.scope;
        x.classList.toggle('active', on);
        x.setAttribute('aria-selected', String(on));
      });
      loadPapers();
    });
    el.addEventListener('keydown', function (ev) {
      var nodes = [].slice.call(document.querySelectorAll('.wp-node'));
      var index = nodes.indexOf(el);
      if (ev.key === 'Enter' || ev.key === ' ') {
        ev.preventDefault();
        el.click();
      } else if (ev.key === 'ArrowRight' && hasKids && !expanded.has(n.path)) {
        expanded.add(n.path);
        renderTree(state.tree, state.stats);
        var rightNode = document.querySelector('.wp-node[data-scope="' + n.scope + '"]');
        if (rightNode) rightNode.focus();
      } else if (ev.key === 'ArrowLeft' && hasKids && expanded.has(n.path)) {
        expanded.delete(n.path);
        renderTree(state.tree, state.stats);
        var leftNode = document.querySelector('.wp-node[data-scope="' + n.scope + '"]');
        if (leftNode) leftNode.focus();
      } else if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        ev.preventDefault();
        var next = nodes[index + (ev.key === 'ArrowDown' ? 1 : -1)];
        if (next) next.focus();
      }
    });
    return el;
  }

  function loadCategories() {
    return fetch('/api/word/categories.txt', { cache: 'no-store' })
      .then(function (r) { return r.text(); })
      .then(function (text) {
        var stats = { papers: 0, uncategorized: 0 };
        var cats = [];
        text.split('\n').forEach(function (line) {
          if (!line) return;
          var p = line.split('\t');
          if (line.charAt(0) === '#') {
            stats.papers = Number(p[1] || 0);
            stats.uncategorized = Number(p[2] || 0);
          } else if (p.length >= 3) {
            cats.push({ depth: Number(p[0] || 0), path: p[1] || '', count: Number(p[2] || 0) });
          }
        });
        cats.forEach(function (c) { if (c.depth === 0) expanded.add(c.path); });
        renderTree(buildTree(cats), stats);
      })
      .catch(function () {
        treeEl.innerHTML = '<div class="wp-node">分类加载失败</div>';
      });
  }

  /* ---------- 条目列表 ---------- */

  function loadPapers() {
    var url = '/api/word/papers.txt?q=' + encodeURIComponent(state.q) +
      '&limit=500' + (state.scope ? '&scope=' + encodeURIComponent(state.scope) : '');
    statusEl.textContent = '正在加载…';
    return fetch(url, { cache: 'no-store' })
      .then(function (r) { return r.text(); })
      .then(function (text) {
        var rows = [];
        var total = 0;
        text.split('\n').forEach(function (line) {
          if (!line) return;
          var p = line.split('\t');
          if (line.charAt(0) === '#') {
            total = Number(p[1] || 0);
          } else if (p.length >= 6) {
            rows.push({ id: p[0], year: p[1], authors: p[2], title: p[3], venue: p[4], hasPdf: p[5] === '1' });
          }
        });
        state.rows = rows;
        state.count = total || rows.length;
        state.anchor = -1;
        state.selected.clear();
        renderRows();
        setStatus(rows.length < state.count ? '显示前 ' + rows.length + ' 篇，输入关键词缩小范围' : '');
      })
      .catch(function () {
        rowsEl.innerHTML = '<div class="wp-empty">无法连接 NPL（请确认桌面程序在运行）</div>';
        statusEl.textContent = '加载失败';
      });
  }

  function renderRows() {
    rowsEl.innerHTML = '';
    if (!state.rows.length) {
      rowsEl.innerHTML = '<div class="wp-empty">这个分类 / 关键词下没有文献</div>';
      setStatus();
      return;
    }
    var frag = document.createDocumentFragment();
    state.rows.forEach(function (row, idx) {
      var el = document.createElement('div');
      el.className = 'wp-row' + (state.selected.has(row.id) ? ' selected' : '');
      el.dataset.index = String(idx);
      el.setAttribute('role', 'row');

      var title = '<span class="c-title">' +
        (row.hasPdf ? '' : '<span class="wp-nopdf">无 PDF</span>') + esc(row.title) + '</span>';
      el.innerHTML = title +
        '<span class="c-author">' + esc(row.authors) + '</span>' +
        '<span class="c-year">' + esc(row.year) + '</span>' +
        '<span class="c-venue">' + esc(row.venue) + '</span>';

      el.addEventListener('click', function (ev) { onRowClick(idx, ev); });
      el.addEventListener('dblclick', function () {
        state.selected.clear();
        state.selected.add(row.id);
        state.anchor = idx;
        renderRows();
        insertNow();
      });
      frag.appendChild(el);
    });
    rowsEl.appendChild(frag);
  }

  function onRowClick(idx, ev) {
    var row = state.rows[idx];
    if (!row) return;
    if (ev.shiftKey && state.anchor >= 0) {
      var a = Math.min(state.anchor, idx), b = Math.max(state.anchor, idx);
      for (var i = a; i <= b; i++) state.selected.add(state.rows[i].id);
    } else if (ev.ctrlKey || ev.metaKey) {
      if (state.selected.has(row.id)) state.selected.delete(row.id);
      else state.selected.add(row.id);
      state.anchor = idx;
    } else {
      state.selected.clear();
      state.selected.add(row.id);
      state.anchor = idx;
    }
    renderRows();
    setStatus();
  }

  /* ---------- 提交 ---------- */

  function postPick(ids) {
    state.sent = true;
    return fetch('/api/word/pick', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ token: token, ids: ids })
    });
  }

  function closeWindow() {
    window.close();
    setTimeout(function () { flash('已发送到 Word，请回到 Word 继续'); }, 120);
  }

  function insertNow() {
    if (!state.selected.size) return;
    if (!token) { flash('缺少 token，无法回传'); return; }
    postPick(Array.from(state.selected))
      .then(closeWindow)
      .catch(function () { state.sent = false; flash('发送失败，请重试'); });
  }

  function cancel() {
    if (token) { postPick([]); }
    closeWindow();
  }

  /* ---------- 事件 ---------- */

  var timer = null;
  $('q').addEventListener('input', function (ev) {
    state.q = ev.target.value;
    clearTimeout(timer);
    timer = setTimeout(loadPapers, 260);
  });
  $('clear').addEventListener('click', function () {
    $('q').value = '';
    state.q = '';
    loadPapers();
    $('q').focus();
  });
  insertBtn.addEventListener('click', insertNow);
  $('cancel').addEventListener('click', cancel);

  document.addEventListener('keydown', function (ev) {
    if (ev.key === 'Escape') { cancel(); }
    else if (ev.key === 'Enter' && document.activeElement.id !== 'q') { ev.preventDefault(); insertNow(); }
  });

  window.addEventListener('beforeunload', function () {
    if (!state.sent && token) {
      try {
        var data = JSON.stringify({ token: token, ids: [] });
        if (navigator.sendBeacon) navigator.sendBeacon('/api/word/pick', new Blob([data], { type: 'application/json' }));
      } catch (e) { /* ignore */ }
    }
  });

  loadCategories();
  loadPapers();
  setStatus();
})();
