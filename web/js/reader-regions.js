/* PDF region selection is separate from the native text selection. */
const clamp = (value, low, high) => Math.max(low, Math.min(value, high));

export function regionRect(bounds, start, end) {
  const x1 = clamp(start.x - bounds.left, 0, bounds.width);
  const x2 = clamp(end.x - bounds.left, 0, bounds.width);
  const y1 = clamp(start.y - bounds.top, 0, bounds.height);
  const y2 = clamp(end.y - bounds.top, 0, bounds.height);
  return {
    x: Math.min(x1, x2) / bounds.width,
    y: Math.min(y1, y2) / bounds.height,
    w: Math.abs(x2 - x1) / bounds.width,
    h: Math.abs(y2 - y1) / bounds.height,
  };
}

export function cropRegion(source, rect, maxSide = 2400) {
  const left = Math.floor(rect.x * source.width);
  const top = Math.floor(rect.y * source.height);
  const width = Math.ceil((rect.x + rect.w) * source.width) - left;
  const height = Math.ceil((rect.y + rect.h) * source.height) - top;
  if (!source.isConnected || width < 1 || height < 1) throw new Error('页面已重绘，请重新框选');
  const scale = Math.min(1, maxSide / Math.max(width, height));
  const crop = document.createElement('canvas');
  crop.width = Math.max(1, Math.round(width * scale));
  crop.height = Math.max(1, Math.round(height * scale));
  const context = crop.getContext('2d', {alpha: false});
  context.fillStyle = '#fff';
  context.fillRect(0, 0, crop.width, crop.height);
  context.drawImage(source, left, top, width, height, 0, 0, crop.width, crop.height);
  return crop;
}

export function copyRegionImage(crop) {
  if (!navigator.clipboard?.write || !window.ClipboardItem) throw new Error('当前窗口无法写入图像剪贴板');
  const png = new Promise((resolve, reject) => crop.toBlob(blob => {
    if (blob) resolve(blob);
    else reject(new Error('无法生成 PNG'));
  }, 'image/png'));
  return navigator.clipboard.write([new ClipboardItem({'image/png': png})]);
}

export function installReaderRegions({pages, scroll, onStart, onAction}) {
  const box = document.createElement('div');
  box.className = 'reader-region-box';
  box.setAttribute('aria-hidden', 'true');
  const toolbar = document.createElement('div');
  toolbar.className = 'selection-toolbar reader-region-tools hidden';
  toolbar.setAttribute('role', 'group');
  toolbar.setAttribute('aria-label', 'PDF 区域操作');
  pages.closest('#reader').append(toolbar);

  let selection = null;
  const clear = () => {
    const id = selection?.pointerId;
    selection = null;
    if (id != null && pages.hasPointerCapture(id)) pages.releasePointerCapture(id);
    box.remove();
    toolbar.classList.add('hidden');
  };
  for (const [action, label] of [
    ['highlight', '高亮区域'], ['copy', '复制图像'], ['translate', 'AI 翻译'],
    ['explain', 'AI 解释'], ['attach', '加入对话'],
  ]) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    button.dataset.regionAction = action;
    button.addEventListener('click', () => {
      if (!selection || selection.pointerId != null) return;
      const selected = selection;
      clear();
      onAction(action, selected);
    });
    toolbar.append(button);
  }

  function update(event) {
    if (selection?.pointerId !== event.pointerId) return;
    const bounds = selection.source.getBoundingClientRect();
    if (!bounds.width || !bounds.height) { clear(); return; }
    const rect = regionRect(bounds, selection.start, {x: event.clientX, y: event.clientY});
    selection.rect = rect;
    box.style.left = `${100 * rect.x}%`;
    box.style.top = `${100 * rect.y}%`;
    box.style.width = `${100 * rect.w}%`;
    box.style.height = `${100 * rect.h}%`;
  }

  pages.addEventListener('contextmenu', event => {
    if (selection || event.target.closest('.pdf-page')) event.preventDefault();
  });
  pages.addEventListener('pointerdown', event => {
    if (event.button !== 2) return;
    const page = event.target.closest('.pdf-page');
    if (!page) return;
    event.preventDefault();
    clear();
    const source = page.querySelector(':scope > canvas');
    if (!source?.width || !source.height) return;
    onStart();
    selection = {source, pageNum: Number(page.dataset.page), pointerId: event.pointerId,
      start: {x: event.clientX, y: event.clientY}};
    pages.setPointerCapture(event.pointerId);
    page.append(box);
    update(event);
  });
  pages.addEventListener('pointermove', update);
  pages.addEventListener('pointerup', event => {
    if (selection?.pointerId !== event.pointerId || event.button !== 2) return;
    update(event);
    if (!selection) return;
    const {source, rect} = selection;
    const bounds = source.getBoundingClientRect();
    if (rect.w * bounds.width < 5 || rect.h * bounds.height < 5) { clear(); return; }
    selection.pointerId = null;
    toolbar.classList.remove('hidden');
    const {width, height} = toolbar.getBoundingClientRect();
    const left = bounds.left + (rect.x + rect.w / 2) * bounds.width - width / 2;
    const top = bounds.top + rect.y * bounds.height;
    const minTop = Math.max(scroll.getBoundingClientRect().top + 4, 8);
    const y = top - height - 8 >= minTop ? top - height - 8 : top + rect.h * bounds.height + 8;
    toolbar.style.left = `${clamp(left, 8, Math.max(8, innerWidth - width - 8))}px`;
    toolbar.style.top = `${clamp(y, minTop, Math.max(minTop, innerHeight - height - 8))}px`;
  });
  for (const eventName of ['pointercancel', 'lostpointercapture']) {
    pages.addEventListener(eventName, event => {
      if (selection?.pointerId === event.pointerId) clear();
    });
  }
  document.addEventListener('pointerdown', event => {
    if (selection && event.button === 0 && !toolbar.contains(event.target)) clear();
  }, true);
  scroll.addEventListener('scroll', clear, {passive: true});
  window.addEventListener('resize', clear);
  return {clear, active: () => Boolean(selection)};
}
