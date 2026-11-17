/* PW Referer Probe：只做一件事——看 downloads.download 带 headers 时会不会真的发 Referer。
 * 不引用 PaperWhale 的任何代码，也不向 PaperWhale 服务发送数据。 */
const BASE = 'http://127.0.0.1:8799';
const out = document.getElementById('out');
const createdIds = [];

function line(text) {
  out.textContent += text + '\n';
}

async function waitForDownload(id, timeout = 15000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    const [item] = await chrome.downloads.search({ id });
    if (!item) return null;
    if (item.state === 'complete' || item.state === 'interrupted') return item;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  return null;
}

function describe(item) {
  if (!item) return 'downloads.search 没查到记录';
  return `state=${item.state} filename=${item.filename} mime=${item.mime || '(空)'} bytes=${item.fileSize || item.bytesReceived || '?'} error=${item.error || ''}`;
}

async function probe(label, options) {
  out.textContent += `\n[${label}] 发起下载…\n`;
  let id;
  try {
    id = await chrome.downloads.download(options);
  } catch (err) {
    line(`  ✗ downloads.download 直接拒绝：${err && err.message ? err.message : String(err)}`);
    line('  → 这说明当前浏览器不接受这个参数（例如 headers 不被支持）。');
    return;
  }
  createdIds.push(id);
  const item = await waitForDownload(id);
  line('  ' + describe(item));
  line('  ↓ 看服务端窗口里对应的那行 JSON：referer 字段是不是 ' + `${BASE}/page`);
}

async function cleanup() {
  let removed = 0;
  for (const id of createdIds) {
    try { await chrome.downloads.removeFile(id); removed += 1; } catch {}
    try { await chrome.downloads.erase({ id }); } catch {}
  }
  createdIds.length = 0;
  line(`\n已清理 ${removed} 个实验文件的磁盘记录（若文件还留在下载目录，可手动删掉 probe*.pdf）。`);
}

document.getElementById('run').addEventListener('click', async () => {
  out.textContent = `探针目标：${BASE}\n`;
  line('实验 1：带 Referer 头');
  await probe('headers 带 Referer', {
    url: `${BASE}/signed.pdf?a=headers`,
    headers: [{ name: 'Referer', value: `${BASE}/page` }],
    conflictAction: 'uniquify',
    saveAs: false,
  });
  line('\n实验 2：对照，不带 headers');
  await probe('不带 headers', {
    url: `${BASE}/signed.pdf?a=control`,
    conflictAction: 'uniquify',
    saveAs: false,
  });
  line('\n全部完成。把这段结果和服务端窗口最后 2 行 JSON 一起发回。');
});

document.getElementById('clean').addEventListener('click', cleanup);
