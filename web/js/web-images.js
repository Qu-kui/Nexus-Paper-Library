/* 联网回答的配图：本地缓存图片 + 点击放大 */
import {escapeHtml} from './api.js';

let installed = false;

function install() {
  if (installed) return;
  installed = true;
  document.addEventListener('click', (event) => {
    const node = event.target.closest('[data-web-image]');
    if (!node) return;
    event.preventDefault();
    openLightbox(node.dataset.webImage, node.dataset.sourceUrl || '');
  });
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    const lightbox = document.querySelector('.web-image-lightbox');
    if (!lightbox) return;
    event.preventDefault();
    event.stopPropagation();
    lightbox.remove();
  }, true);
}

function openLightbox(url, sourceUrl) {
  document.querySelector('.web-image-lightbox')?.remove();
  const root = document.createElement('div');
  root.className = 'web-image-lightbox';
  root.innerHTML = `<div class="web-image-lightbox-body">
    <img src="${escapeHtml(url)}" alt="配图" />
    <div class="web-image-lightbox-foot">
      ${sourceUrl ? `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noreferrer">打开来源</a>` : ''}
      <button type="button" class="btn tiny ghost" data-close>关闭</button>
    </div>
  </div>`;
  root.addEventListener('click', (event) => {
    if (event.target === root || event.target.closest('[data-close]')) root.remove();
  });
  const reader = document.querySelector('#reader');
  const host = reader && !reader.classList.contains('hidden') ? reader : document.body;
  host.append(root);
}

export function webImagesHtml(images) {
  const rows = (images || []).filter((item) => item && item.url);
  if (!rows.length) return '';
  install();
  return `<div class="msg-images">${rows.map((item) => `
    <figure class="msg-image">
      <img loading="lazy" src="${escapeHtml(item.url)}" alt="${escapeHtml(item.title || '配图')}" data-web-image="${escapeHtml(item.url)}" data-source-url="${escapeHtml(item.source_url || '')}" />
      <figcaption>${escapeHtml(item.title || '')}${item.source_url ? ` · <a href="${escapeHtml(item.source_url)}" target="_blank" rel="noreferrer">来源</a>` : ''}</figcaption>
    </figure>`).join('')}</div>`;
}
