/* Local, reproducible rendering of AI-authored diagram and chart descriptions. */
import {api, toast} from './api.js';

const cache = new Map();
const libraries = new Map();
const results = new WeakMap();
let diagramSerial = 0;
let diagramQueue = Promise.resolve();

function loadLibrary(name, url) {
  if (window[name]) return Promise.resolve(window[name]);
  if (!libraries.has(name)) {
    libraries.set(name, new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = url;
      script.onload = () => {
        if (window[name]) resolve(window[name]);
        else { libraries.delete(name);script.remove();reject(new Error('图形组件加载失败')); }
      };
      script.onerror = () => { libraries.delete(name);script.remove();reject(new Error('图形组件加载失败，请重试')); };
      document.head.append(script);
    }));
  }
  return libraries.get(name);
}

const text = (value, limit = 160) => String(value ?? '').slice(0, limit);
function numbers(data) {
  if (!Array.isArray(data) || !data.length || data.length > 200 ||
      data.some(value => value !== null && (typeof value !== 'number' || !Number.isFinite(value)))) {
    throw new Error('每组数据需要 1–200 个数值或空值');
  }
  return data;
}

export function chartOption(spec) {
  if (!spec || !['line', 'bar', 'scatter', 'pie'].includes(spec.type)) throw new Error('支持折线图、柱状图、散点图和饼图');
  if (!text(spec.source).trim()) throw new Error('图表需要注明数据来源，可在代码的 source 字段中补充');
  if (!Array.isArray(spec.series) || !spec.series.length || spec.series.length > 6) throw new Error('图表需要 1–6 组数据');
  const categories = Array.isArray(spec.labels) ? spec.labels.map(value => text(value, 100)) : [];
  if (spec.type !== 'scatter' && (!categories.length || categories.length > 200)) throw new Error('请提供与数据对应的 labels');
  const series = spec.series.map(item => {
    if (spec.type === 'scatter') {
      if (!Array.isArray(item.data) || !item.data.length || item.data.length > 200 ||
          item.data.some(point => !Array.isArray(point) || point.length !== 2 || point.some(value => typeof value !== 'number' || !Number.isFinite(value)))) {
        throw new Error('散点图需要数值坐标对，例如 [[1,2],[2,3]]');
      }
    } else {
      numbers(item.data);
      if (item.data.length !== categories.length) throw new Error('数据数量需要与 labels 一致');
    }
    return {name:text(item.name, 80), type:spec.type, data:item.data, symbolSize:7,
      ...(spec.type === 'line' ? {connectNulls:false} : {})};
  });
  if (spec.type === 'pie') {
    if (series.length !== 1 || series[0].data.some(value => value === null || value < 0) || !series[0].data.some(value => value > 0)) {
      throw new Error('饼图需要一组非负数值且总和大于零');
    }
    series[0] = {...series[0], radius:['0%', '55%'], center:['50%', '49%'],
      data:series[0].data.map((value, index) => ({name:categories[index], value})),
      label:{formatter:'{b}: {c}', width:140, overflow:'break'}};
  }
  return {
    animation:false, backgroundColor:'#ffffff',
    color:['#800050', '#147d88', '#7b6bb5', '#c48a24', '#3674ac', '#9a6676'],
    textStyle:{fontFamily:'Microsoft YaHei, Arial, sans-serif', color:'#26354a'},
    title:{text:text(spec.title) || '数据图表', left:16, top:12, textStyle:{fontSize:17, width:440, overflow:'truncate'}},
    legend:{bottom:48, textStyle:{fontSize:12}},
    graphic:[{type:'text', left:16, bottom:10, style:{
      text:'来源 '+text(spec.source, 300), fill:'#59677b', fontSize:11, width:445, overflow:'break',
    }}],
    ...(spec.type === 'pie' ? {} : {
      grid:{top:64, left:38, right:24, bottom:102, containLabel:true},
      xAxis:{type:spec.type === 'scatter' ? 'value' : 'category', data:categories,
        name:text(spec.xLabel, 80), nameLocation:'middle', nameGap:38, axisLabel:{width:90, overflow:'truncate'}},
      yAxis:{type:'value', name:text(spec.yLabel, 80), splitLine:{lineStyle:{color:'#e6eaf0'}}},
    }),
    series,
  };
}

async function renderGraphic(kind, source) {
  if (source.length > 30000) throw new Error('图形代码过长，请减少节点或数据点');
  if (kind === 'chart') {
    const option = chartOption(JSON.parse(source));
    const echarts = await loadLibrary('echarts', '/vendor/echarts/echarts.min.js');
    const chart = echarts.init(null, null, {renderer:'svg', ssr:true, width:480, height:340});
    try {
      chart.setOption(option);
      return {svg:chart.renderToSVGString(), title:option.title.text};
    } finally { chart.dispose(); }
  }
  if (/%%\s*\{|^\s*---|<\s*\/?\s*(?:script|iframe|img|svg)|\bclick\s+/im.test(source)) {
    throw new Error('请使用普通 Mermaid 图形语法，样式由应用统一设置');
  }
  if (source.split('\n').length > 180) throw new Error('图形节点过多，请拆分为几张图');
  const mermaid = await loadLibrary('mermaid', '/vendor/mermaid/mermaid.tiny.js');
  // Mermaid measures labels in a temporary DOM. Serialize its rendering work.
  const operation = diagramQueue.then(async () => {
    mermaid.initialize({startOnLoad:false, securityLevel:'strict', suppressErrorRendering:true, htmlLabels:false,
      maxTextSize:30000, maxEdges:160, theme:'base', fontFamily:'Microsoft YaHei, Arial, sans-serif',
      flowchart:{htmlLabels:false, useMaxWidth:true}, themeVariables:{
        primaryColor:'#f9edf4', primaryTextColor:'#26354a', primaryBorderColor:'#800050',
        lineColor:'#65758a', secondaryColor:'#edf6f7', tertiaryColor:'#ffffff',
      }});
    const {svg} = await mermaid.render('npl-diagram-' + (++diagramSerial), source);
    return {svg, title:'流程与关系图'};
  });
  diagramQueue = operation.catch(() => {});
  return operation;
}

function standaloneSVG(svg) {
  const document = new DOMParser().parseFromString(svg, 'image/svg+xml');
  if (document.querySelector('parsererror')) throw new Error('图形格式异常');
  const root = document.documentElement;
  const viewBox = root.getAttribute('viewBox')?.split(/[\s,]+/).map(Number);
  if (viewBox?.length === 4 && viewBox.every(Number.isFinite) && viewBox[2] > 0 && viewBox[3] > 0) {
    root.setAttribute('width', String(viewBox[2]));
    root.setAttribute('height', String(viewBox[3]));
  }
  // Export the same static image used by the conversation.
  document.querySelectorAll('script,foreignObject,image,iframe,object,embed').forEach(node => node.remove());
  for (const node of document.querySelectorAll('*')) {
    for (const attribute of [...node.attributes]) {
      if (/^on/i.test(attribute.name) || (/href$/i.test(attribute.name) && !attribute.value.startsWith('#'))) node.removeAttribute(attribute.name);
      if ([...attribute.value.matchAll(/url\(\s*(['"]?)(.*?)\1\s*\)/gi)].some(match => !match[2].trim().startsWith('#'))) {
        node.removeAttribute(attribute.name);
      }
    }
    if (node.tagName === 'style' && (/@import/i.test(node.textContent) ||
        [...node.textContent.matchAll(/url\(\s*(['"]?)(.*?)\1\s*\)/gi)].some(match => !match[2].trim().startsWith('#')))) node.remove();
  }
  root.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  return new XMLSerializer().serializeToString(root);
}

function cachedGraphic(kind, source) {
  const key = kind + '\n' + source;
  if (!cache.has(key)) {
    const task = renderGraphic(kind, source).then(result => ({...result, svg:standaloneSVG(result.svg)}));
    cache.set(key, task);
    if (cache.size > 24) cache.delete(cache.keys().next().value);
  }
  return cache.get(key);
}

const imageURL = svg => 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);

async function renderFigure(figure) {
  if (figure.dataset.visualState) return;
  figure.dataset.visualState = 'rendering';
  const source = figure.querySelector('code')?.textContent || '';
  const status = figure.querySelector('.ai-visual-status');
  try {
    const result = await cachedGraphic(figure.dataset.visualKind, source);
    if (!figure.isConnected) return;
    results.set(figure, result);
    const image = new Image();
    image.className = 'ai-visual-image';
    image.alt = result.title;
    image.src = imageURL(result.svg);
    const caption = document.createElement('figcaption');
    caption.textContent = result.title;
    const tools = document.createElement('div');
    tools.className = 'ai-visual-tools';
    for (const [action, label] of [['expand','放大'], ['svg','导出 SVG'], ['png','导出 PNG']]) {
      const button = document.createElement('button');
      button.type = 'button';button.dataset.visualAction = action;button.textContent = label;
      tools.append(button);
    }
    status.replaceWith(caption, image, tools);
    figure.dataset.visualState = 'ready';
  } catch (error) {
    if (!figure.isConnected) return;
    figure.dataset.visualState = 'error';
    status.textContent = '图形生成失败，可展开代码核对后让 AI 修正。'+text(error.message, 150);
    const retry = document.createElement('button');
    retry.type = 'button';retry.textContent = '重试';retry.dataset.visualAction = 'retry';
    status.append(retry);
  }
}

async function fileToBase64(blob) {
  const buffer = await blob.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  let binary = '';
  const chunk = 0x8000;
  for (let index = 0; index < bytes.length; index += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(index, index + chunk));
  }
  return btoa(binary);
}

async function saveExport(blob, name) {
  const dataBase64 = await fileToBase64(blob);
  const result = await api.post('/export/save', {filename: name, data_base64: dataBase64});
  toast(`已导出：${result.name}（${result.folder}）`, 'success', 6000, {
    label: '打开文件夹',
    onClick: () => { api.post('/export/open', {}).catch(() => {}); },
  });
  return result;
}

function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;link.download = name;link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function exportPNG(result) {
  const image = new Image();
  image.src = imageURL(result.svg);
  await image.decode();
  const scale = Math.min(2, 4096 / Math.max(image.naturalWidth, image.naturalHeight));
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
  canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
  const context = canvas.getContext('2d');
  context.fillStyle = '#fff';context.fillRect(0, 0, canvas.width, canvas.height);
  context.drawImage(image, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
  if (!blob) throw new Error('PNG 导出失败');
  return blob;
}

function expandGraphic(result) {
  const dialog = document.createElement('dialog');
  dialog.className = 'ai-visual-dialog';
  dialog.setAttribute('aria-modal', 'true');
  dialog.setAttribute('aria-label', result.title);
  const header = document.createElement('div');
  header.className = 'ai-visual-dialog-head';
  const title = document.createElement('strong');title.textContent = result.title;
  const close = document.createElement('button');close.type = 'button';close.textContent = '关闭';
  close.addEventListener('click', () => dialog.close());
  header.append(title, close);
  const image = new Image();image.src = imageURL(result.svg);image.alt = result.title;
  dialog.append(header, image);document.body.append(dialog);
  dialog.addEventListener('close', () => dialog.remove(), {once:true});
  dialog.addEventListener('keydown', event => {
    if (event.key === 'Escape') event.stopPropagation();
    if (event.key === 'Tab') { event.preventDefault();close.focus(); }
  });
  dialog.showModal();close.focus();
}

export function initAnswerVisuals() {
  function scan(root) {
    if (!(root instanceof Element)) return;
    if (root.matches('.ai-visual')) renderFigure(root);
    root.querySelectorAll('.ai-visual:not([data-visual-state])').forEach(renderFigure);
  }
  const observer = new MutationObserver(records => {
    for (const record of records) for (const node of record.addedNodes) scan(node);
  });
  observer.observe(document.body, {childList:true, subtree:true});
  scan(document.body);
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-visual-action]');
    const figure = button?.closest('.ai-visual');
    if (!figure) return;
    event.stopPropagation();
    const action = button.dataset.visualAction;
    if (action === 'retry') {
      cache.delete(figure.dataset.visualKind + '\n' + (figure.querySelector('code')?.textContent || ''));
      delete figure.dataset.visualState;renderFigure(figure);return;
    }
    const result = results.get(figure);
    if (!result) return;
    if (action === 'expand') { expandGraphic(result);return; }
    button.disabled = true;
    try {
      const name = result.title.replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').slice(0, 80) || 'NPL 图形';
      if (action === 'svg') await saveExport(new Blob([result.svg], {type:'image/svg+xml;charset=utf-8'}), name+'.svg');
      else await saveExport(await exportPNG(result), name+'.png');
    } catch (error) { toast(error.message, 'error'); }
    finally { button.disabled = false; }
  });
}
