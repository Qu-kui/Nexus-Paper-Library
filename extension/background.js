/* NPL Connector: 接住用户手动点击“Download PDF”后产生的浏览器下载。 */
const DEFAULT_PORTS = Array.from({ length: 30 }, (_, i) => 8765 + i);

// service worker 与 popup.js 是两个独立入口，不能跨文件引用，所以这里自带一份。
// Chrome 扩展 API 的拒绝值不一定是 Error；FastAPI 422 的 detail 还是数组，
// 直接 String() 会变成 [object Object]，用户就看不出到底错在哪。
function formatError(err) {
  if (err == null) return '未知错误';
  if (typeof err === 'string') return err;
  if (err instanceof Error || (typeof err.message === 'string' && err.message)) return String(err.message);
  try {
    const json = JSON.stringify(err);
    if (json && json !== '{}') return json;
  } catch { /* 无法序列化时退回兜底文案 */ }
  return '未知错误（浏览器返回了无法解析的错误对象）';
}

async function waitForDownload(id, timeout = 120000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    const [item] = await chrome.downloads.search({ id });
    if (!item) return null;
    if (item.state === 'complete') return item;
    if (item.state === 'interrupted') return null;
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
  return null;
}

async function importDownloadedPdf(watch, item) {
  const body = {
    ...(watch.meta || {}),
    url: (watch.meta && watch.meta.url) || watch.pageUrl || '',
    pdf_download_path: item.filename,
    download_pdf: false,
    analyze: false,
  };
  const response = await fetch(watch.base + '/api/import/web', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`HTTP ${response.status}：${formatError(data && data.detail)}`);
  const paper = data.paper || {};
  return {
    status: data.pdf === 'attached' && paper.has_pdf ? 'ok' : 'saved_without_pdf',
    title: paper.title || body.title || '',
    paperId: paper.id || '',
    message: data.pdf_warning || '',
  };
}

chrome.downloads.onCreated.addListener(async (item) => {
  const stored = await chrome.storage.local.get(['pwWatch']);
  const watch = stored.pwWatch;
  if (!watch || !watch.base || Date.now() - Number(watch.at || 0) > 15 * 60 * 1000) return;
  const filename = String(item.filename || '');
  const url = String(item.url || '');
  if (!/\.pdf$/i.test(filename) && !/\.pdf(?:[?#]|$)/i.test(url)) return;
  const done = await waitForDownload(item.id);
  if (!done || !done.filename) {
    await chrome.storage.local.set({ pwWatchResult: { status: 'failed', message: '下载没有完成，可能被浏览器拦截。' } });
    return;
  }
  try {
    const result = await importDownloadedPdf(watch, done);
    await chrome.storage.local.set({ pwWatchResult: { ...result, at: Date.now() } });
    await chrome.storage.local.remove('pwWatch');
  } catch (err) {
    await chrome.storage.local.set({
      pwWatchResult: { status: 'failed', message: formatError(err), at: Date.now() },
    });
  }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message && message.type === 'pw-watch-download') {
    chrome.storage.local.set({ pwWatch: message.watch, pwWatchResult: null }).then(() => sendResponse({ ok: true }));
    return true;
  }
  return false;
});
