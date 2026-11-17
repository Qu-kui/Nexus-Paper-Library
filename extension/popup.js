/* NPL Connector popup */
const DEFAULT_PORTS = Array.from({ length: 30 }, (_, i) => 8765 + i);
const state = { base: "", tab: null, meta: null, ports: DEFAULT_PORTS, saved: false, pdfBlocked: false, debuggerNote: "" };

const $ = (id) => document.getElementById(id);

function setConn(text, cls = "") {
  const el = $("conn-state");
  el.textContent = text;
  el.className = "conn " + cls;
}

function showError(message) {
  const el = $("error");
  el.textContent = message;
  el.classList.remove("hidden");
}

// Chrome 扩展 API 的拒绝值不一定是 Error，直接 String() 会得到 [object Object]，
// 所以统一转成人能看懂的文字，绝不吞掉原因。
function formatError(err) {
  if (err == null) return "未知错误";
  if (typeof err === "string") return err;
  if (err instanceof Error || (typeof err.message === "string" && err.message)) return String(err.message);
  try {
    const json = JSON.stringify(err);
    if (json && json !== "{}") return json;
  } catch { /* 无法序列化时退回 String */ }
  return "未知错误（浏览器返回了无法解析的错误对象）";
}

function clearError() {
  $("error").classList.add("hidden");
}

/* 在目标页面里执行：抓取文献元数据（必须完全自包含） */
async function extractPageMetadata({expandPdfMenu=false, expectedUrl=""}={}) {
  if(expectedUrl && location.href!==expectedUrl)throw new Error("Page changed; reopen connector.");
  // executeScript 只注入函数体，外层作用域不会跟着走；这两个辅助函数必须写在函数内部。
  function isArticleLandingUrl(rawUrl, pageUrl) {
    try {
      const target = new URL(rawUrl, pageUrl);
      const page = new URL(pageUrl);
      if (!/^https?:$/.test(target.protocol)) return true;
      if (target.href.replace(/#.*$/, '') === page.href.replace(/#.*$/, '')) return true;
      // 文章落地页（/science/article/pii/XXX 或 /abs/pii/XXX）不是 PDF 地址，
      // 把它填进“PDF 地址”只会让抓取去请求一个 HTML 页面。
      return /^\/science\/article\/(?:abs\/)?pii\/[A-Z0-9]+\/?$/i.test(target.pathname) && !target.search;
    } catch {
      return true;
    }
  }
  function authorsFromPreloadedState(container) {
    const names = [];
    const seen = new Set();
    const visit = (node, depth = 0) => {
      if (!node || typeof node !== 'object' || depth > 24 || names.length >= 40) return;
      if (Array.isArray(node)) {
        node.forEach((item) => visit(item, depth + 1));
        return;
      }
      if (node['#name'] === 'author') {
        const parts = [];
        for (const child of node['$$'] || []) {
          const kind = child && child['#name'];
          if ((kind === 'given-name' || kind === 'surname' || kind === 'suffix') && typeof child['_'] === 'string' && child['_'].trim()) {
            parts.push(child['_'].trim());
          }
        }
        const name = parts.join(' ');
        if (name && !seen.has(name)) {
          seen.add(name);
          names.push(name);
        }
        return;
      }
      for (const value of Object.values(node)) visit(value, depth + 1);
    };
    visit(container);
    return names;
  }
  // ScienceDirect 的 pdfDownload/#pdfLink 常在页面 hydrate 后才出现；等它一下再扫。
  if (expandPdfMenu) {
    for (let attempt = 0; attempt < 8; attempt += 1) {
      const html = document.documentElement.outerHTML;
      if (html.includes('"pdfDownload"')
        || document.querySelector('#pdfLink[href], .pdf-download-btn-link[href], .PdfDropDownMenu a[href]')) break;
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
  }
  const text = (value) => (value || "").replace(/\s+/g, " ").trim();
  const metas = {};
  document.querySelectorAll("meta").forEach((node) => {
    const key = (
      node.getAttribute("name") ||
      node.getAttribute("property") ||
      node.getAttribute("itemprop") ||
      ""
    ).toLowerCase();
    const content = text(node.getAttribute("content"));
    if (!key || !content) return;
    if (!metas[key]) metas[key] = [];
    metas[key].push(content);
  });
  const one = (...keys) => {
    for (const key of keys) {
      const values = metas[key.toLowerCase()];
      if (values && values.length && values[0]) return values[0];
    }
    return "";
  };
  const many = (key) => metas[key.toLowerCase()] || [];

  let jsonld = [];
  document.querySelectorAll('script[type="application/ld+json"]').forEach((node) => {
    try {
      const parsed = JSON.parse(node.textContent || "null");
      const walk = (value) => {
        if (!value) return;
        if (Array.isArray(value)) {
          value.forEach(walk);
        } else if (typeof value === "object") {
          jsonld.push(value);
          if (value["@graph"]) walk(value["@graph"]);
        }
      };
      walk(parsed);
    } catch (err) {
      /* 忽略坏 JSON-LD */
    }
  });
  const isArticle = (node) => {
    const type = node["@type"];
    const list = Array.isArray(type) ? type : [type];
    return list.some((item) => /Article|Scholarly|Publication|Book|Chapter/i.test(String(item || "")));
  };
  const article = jsonld.find(isArticle) || jsonld[0] || {};

  const authorNames = [];
  many("citation_author").forEach((name) => authorNames.push(text(name)));
  const ldAuthors = article.author || article.creator;
  if (ldAuthors) {
    (Array.isArray(ldAuthors) ? ldAuthors : [ldAuthors]).forEach((item) => {
      if (typeof item === "string") authorNames.push(text(item));
      else if (item && item.name) authorNames.push(text(item.name));
    });
  }
  const metaAuthor = one("author", "dc.creator", "citation_authors");
  if (!authorNames.length && metaAuthor) authorNames.push(text(metaAuthor));

  let title = one(
    "citation_title",
    "dc.title",
    "dcterms.title",
    "og:title",
    "twitter:title",
    "eprints.title",
  );
  if (!title && article.headline) title = text(article.headline);
  if (!title && article.name) title = text(article.name);
  if (!title) title = text(document.title);

  const siteName = one("og:site_name");
  if (siteName && title) {
    const suffixes = [" - " + siteName, " | " + siteName, " — " + siteName];
    for (const suffix of suffixes) {
      if (title.endsWith(suffix)) {
        title = title.slice(0, -suffix.length).trim();
        break;
      }
    }
  }

  let doi = one("citation_doi", "doi", "dc.identifier", "prism.doi").replace(/^https?:\/\/doi\.org\//i, "");
  if (!doi) {
    const match = (location.href + " " + (document.body ? document.body.innerText.slice(0, 20000) : "")).match(
      /10\.\d{4,9}\/[-._;()/:a-z0-9]+/i,
    );
    if (match) doi = match[0].replace(/[.,;)]+$/, "");
  }

  let arxivId = "";
  const arxivMatch = location.href.match(/arxiv\.org\/(?:abs|pdf)\/([^/?#]+?)(?:\.pdf)?(?:[?#]|$)/i);
  if (arxivMatch) arxivId = arxivMatch[1];
  const arxivDoi = doi.match(/10\.48550\/arxiv\.(.+)/i);
  if (!arxivId && arxivDoi) arxivId = arxivDoi[1];

  const abs = (value) => {
    if (!value) return "";
    try {
      return new URL(value, location.href).href;
    } catch (err) {
      return value;
    }
  };

  const pdfCandidates = [];
  const supplementalCandidates = new Set();
  const trusted = value => {try {const u=new URL(value);return /^(?:www\.)?sciencedirect\.com$/i.test(u.hostname)?u:null;}catch{return null;}};
  const pii=location.pathname.match(/^\/science\/article\/(?:abs\/)?pii\/([A-Z0-9]+)(?:\/|$)/i)?.[1];
  const evidence=[one('citation_public_url'),document.querySelector('link[rel="canonical"]')?.href].map(trusted).some(u=>u&&pii&&u.pathname.match(/\/pii\/([A-Z0-9]+)(?:\/|$)/i)?.[1]===pii);
  const scienceDirect=Boolean(trusted(location.href)||evidence);
  if (scienceDirect && !authorNames.length) {
    // ScienceDirect 页面没有 citation_author 元数据，作者只在 __PRELOADED_STATE__ 里。
    try {
      for (const name of authorsFromPreloadedState(window.__PRELOADED_STATE__)) authorNames.push(name);
    } catch { /* 页面没有预加载状态时继续用其他来源 */ }
  }
  if(scienceDirect&&expandPdfMenu) {
    const toggle=document.querySelector('#pdfLink, button[aria-controls*="pdf" i]');
    const href=toggle?.getAttribute('href');
    const safe=toggle&&(toggle.tagName==='BUTTON'||(toggle.tagName==='A'&&(!href||href==='#')));
    const open=toggle?.getAttribute('aria-expanded')==='true'||Array.from(document.querySelectorAll('.PdfDropDownMenu')).some(n=>n.getClientRects().length&&n.querySelector('a[href]:not([href="#"])'));
    if(safe&&!open) {
      const prevent=event=>event.preventDefault();toggle.addEventListener('click',prevent,true);
      try {
        await new Promise(resolve=>{
          let timer;const finish=()=>{clearTimeout(timer);observer.disconnect();resolve();};
          const observer=new MutationObserver(()=>{if(document.querySelector('.PdfDropDownMenu a[href]:not([href="#"]), #pdfLink[href]:not([href="#"])'))finish();});
          observer.observe(document.documentElement,{subtree:true,childList:true,attributes:true,attributeFilter:['href']});
          timer=setTimeout(finish,1500);toggle.click();
        });
        document.querySelectorAll('#pdfLink[href], .PdfDropDownMenu a[href], .pdf-download-btn-link[href], a[href*="/pdfft"], a[href*="md5="], a[href*="download=true"]').forEach(node=>{const v=node.getAttribute('href');if(v&&v!=='#'){pdfCandidates.push(abs(v));if(/supplement|supporting information/i.test(node.textContent||''))supplementalCandidates.add(abs(v));}});
      } finally {
        const expanded=toggle.getAttribute('aria-expanded');
        const visible=Array.from(document.querySelectorAll('.PdfDropDownMenu')).some(n=>n.getClientRects().length&&n.querySelector('a[href]:not([href="#"])'));
        if(expanded==='true'||(expanded===null&&visible))toggle.click();
        toggle.removeEventListener('click',prevent,true);
      }
    }
  }
  if (scienceDirect) {
    document.querySelectorAll('#pdfLink[href], .pdf-download-btn-link[href], .PdfDropDownMenu a[href], .PdfEmbed object[data], a[href*="/pdfft"], a[href*="md5="], a[href*="download=true"]').forEach(node=>{const v=node.getAttribute('href')||node.getAttribute('data');if(v&&v!=='#'){pdfCandidates.push(abs(v));if(/supplement|supporting information/i.test(node.textContent||''))supplementalCandidates.add(abs(v));}});
    const addJsonPdf = (value, depth=0) => {
      if (!value || depth > 6) return;
      if (Array.isArray(value)) { value.forEach(item => addJsonPdf(item, depth + 1)); return; }
      if (typeof value !== 'object') return;
      const info = value.pdfDownload && value.pdfDownload.urlMetadata;
      if (info && typeof info === 'object') {
        try {
          const path=String(info.path||'').replace(/^\/+|\/+$/g,'');
          if (/^[\w/-]+$/.test(path) && /^[A-Z0-9]+$/.test(String(info.pii||'')) && ['/pdfft','.pdf'].includes(info.pdfExtension)
            && info.queryParams && typeof info.queryParams.md5==='string' && typeof info.queryParams.pid==='string') {
            pdfCandidates.push(abs('/'+path+'/'+info.pii+info.pdfExtension+'?'+new URLSearchParams(info.queryParams)));
          }
        } catch { /* optional */ }
      }
      for (const key of Object.keys(value)) addJsonPdf(value[key], depth + 1);
    };
    document.querySelectorAll('script[type="application/json"]').forEach(node => {
      try { addJsonPdf(JSON.parse(node.textContent)); } catch { /* optional */ }
    });

  }
  many("citation_pdf_url").forEach((url) => pdfCandidates.push(abs(url)));
  document.querySelectorAll('link[rel="alternate"][type="application/pdf"], link[type="application/pdf"]').forEach((node) => {
    pdfCandidates.push(abs(node.getAttribute("href")));
  });
  ["og:pdf", "twitter:player:stream"].forEach((key) => {
    const url = one(key);
    if (url) pdfCandidates.push(abs(url));
  });
  document.querySelectorAll('embed[type="application/pdf"], iframe[src*=".pdf"]').forEach((node) => {
    pdfCandidates.push(abs(node.getAttribute("src")));
  });
  if (!pdfCandidates.length && arxivId) pdfCandidates.push("https://arxiv.org/pdf/" + arxivId);
  // IEEE Xplore 等出版社：PDF 按钮常是 stamp/stamp.jsp 或 stampPDF/getPDF.jsp
  document.querySelectorAll('a[href*="stampPDF/getPDF.jsp"], a[href*="stamp/stamp.jsp"], a[download][href]').forEach((node) => {
    const href = node.getAttribute("href") || "";
    if (!href) return;
    if (href.includes("stamp/stamp.jsp")) {
      pdfCandidates.push(abs(href.replace("stamp/stamp.jsp", "stampPDF/getPDF.jsp")));
    } else if (/stampPDF\/getPDF\.jsp/i.test(href) || /\.pdf(\?|#|$)/i.test(href)) {
      pdfCandidates.push(abs(href));
    }
  });
  document.querySelectorAll('a[href*=".pdf"], a[href*="/pdf/"]').forEach(node=>{
    const href=node.getAttribute('href')||'';
    if(/\.pdf(?:[?#]|$)|\/pdf\//i.test(href)){pdfCandidates.push(abs(href));if(/supplement|supporting information/i.test(node.textContent||''))supplementalCandidates.add(abs(href));}
  });
  if(scienceDirect) {
    const pii=location.pathname.match(/^\/science\/article\/(?:abs\/)?pii\/([A-Z0-9]+)\/?$/);
    if(pii)pdfCandidates.push(abs('/science/article/pii/'+pii[1]+'/pdfft?download=true'));
  }
  // Zotero ScienceDirect translator / DevTools fetcher 都直接扫描整页 HTML 里的 pdfDownload 元数据。
  // 它不一定在 script[type=application/json] 中，所以这里单独按大括号配对提取。
  if (scienceDirect) {
    try {
      const html = document.documentElement.outerHTML;
      const marker = html.indexOf('"pdfDownload"');
      if (marker >= 0) {
        const start = html.indexOf('{', marker);
        let depth = 0, quote = '', escaped = false, end = -1;
        for (let i = start; i < html.length; i += 1) {
          const ch = html[i];
          if (quote) {
            if (escaped) escaped = false;
            else if (ch === '\\') escaped = true;
            else if (ch === quote) quote = '';
            continue;
          }
          if (ch === '"' || ch === "'") { quote = ch; continue; }
          if (ch === '{') depth += 1;
          else if (ch === '}') {
            depth -= 1;
            if (depth === 0) { end = i + 1; break; }
          }
        }
        if (end > start) {
          const info = JSON.parse(html.slice(start, end));
          const meta = info && info.urlMetadata ? info.urlMetadata : info;
          const qp = meta && meta.queryParams ? meta.queryParams : {};
          const pii = String(meta.pii || '');
          const path = String(meta.path || '').replace(/^\/+|\/+$/g, '');
          const ext = String(meta.pdfExtension || '');
          if (/^[A-Z0-9]+$/.test(pii) && /^[\w/-]+$/.test(path) && ['/pdfft', '.pdf'].includes(ext) && qp.md5 && qp.pid) {
            const query = new URLSearchParams({ md5: String(qp.md5), pid: String(qp.pid) }).toString();
            pdfCandidates.unshift(abs('/' + path + '/' + pii + ext + '?' + query));
          }
        }
      }
      for (const match of html.matchAll(/https:\/\/pdf\.sciencedirectassets\.com\/[^"'<>\s]+/gi)) {
        pdfCandidates.unshift(match[0].replace(/&amp;/g, '&'));
      }
    } catch { /* 页面没有该元数据时继续用其他候选 */ }
  }

  let cleanPdf = [...new Set(pdfCandidates.filter((url) => /^https?:/i.test(url) && !url.includes("citation_pdf_url")))].sort((a,b)=>Number(supplementalCandidates.has(a)||/supplement|mmc\d|\/suppl/i.test(a))-Number(supplementalCandidates.has(b)||/supplement|mmc\d|\/suppl/i.test(b))).slice(0,12);
  cleanPdf = cleanPdf.filter((url) => !isArticleLandingUrl(url, location.href));
  if (scienceDirect) {
    const currentPii = location.pathname.match(/^\/science\/article\/(?:abs\/)?pii\/([A-Z0-9]+)(?:\/|$)/i)?.[1] || '';
    cleanPdf = cleanPdf.filter((rawUrl) => {
      try {
        const parsed = new URL(rawUrl);
        const host = parsed.hostname.toLowerCase();
        if (host.includes('scopus.com')) return false;
        const pii = parsed.pathname.match(/\/pii\/([A-Z0-9]+)/i)?.[1] || '';
        return !pii || !currentPii || pii === currentPii;
      } catch { return false; }
    });
    cleanPdf = [...new Set(cleanPdf)].slice(0, 6);
  }

  const date = one(
    "citation_publication_date",
    "citation_date",
    "citation_online_date",
    "dc.date",
    "dcterms.date",
    "prism.publicationdate",
    "article:published_time",
  );
  let year = "";
  if (date) {
    const match = date.match(/(19|20)\d{2}/);
    if (match) year = match[0];
  }
  if (!year && article.datePublished) {
    const match = String(article.datePublished).match(/(19|20)\d{2}/);
    if (match) year = match[0];
  }

  const venue =
    one(
      "citation_journal_title",
      "citation_conference_title",
      "citation_inbook_title",
      "citation_dissertation_institution",
      "dc.source",
      "dcterms.ispartof",
      "prism.publicationname",
    ) || (typeof article.isPartOf === "string" ? text(article.isPartOf) : "") ||
    (article.isPartOf && article.isPartOf.name ? text(article.isPartOf.name) : "");

  let abstract = one(
    "citation_abstract",
    "dc.description",
    "dcterms.abstract",
    "description",
    "og:description",
    "twitter:description",
  );
  if (!abstract && article.abstract) abstract = text(article.abstract);
  if (!abstract && article.description) abstract = text(article.description);

  const keywords = [];
  many("citation_keywords").forEach((item) => keywords.push(item));
  many("keywords").forEach((item) => {
    item.split(/[,;，；]/).forEach((part) => keywords.push(text(part)));
  });
  if (article.keywords) {
    (Array.isArray(article.keywords) ? article.keywords : String(article.keywords).split(/[,;，；]/)).forEach((item) =>
      keywords.push(text(item)),
    );
  }

  // 卷 / 期 / 页码：参考文献排版要用；不同出版社用 highwire 或 prism 两套 meta 名。
  const volumeRaw = one("citation_volume", "prism.volume", "prism:volume");
  const issueRaw = one("citation_issue", "prism.number", "prism:issue", "prism:number", "prism.issue");
  const firstPage = one("citation_firstpage", "prism.startingpage", "prism:startingpage");
  const lastPage = one("citation_lastpage", "prism.endingpage", "prism:endingpage");
  const volume = text(volumeRaw).replace(/^(?:vol(?:ume)?\.?|第)\s*/i, "").replace(/[卷]$/, "").trim();
  const issue = text(issueRaw).replace(/[()（）]/g, "").replace(/^(?:no\.?|issue|第)\s*/i, "").replace(/[期]$/, "").trim();
  let pages = "";
  if (text(firstPage)) {
    const first = text(firstPage);
    const last = text(lastPage);
    pages = last && last !== first ? `${first}–${last}` : first;
  }

  return {
    title: text(title),
    authors: authorNames.map(text).filter(Boolean),
    year,
    venue: text(venue),
    doi: text(doi),
    arxiv_id: text(arxivId),
    volume,
    issue,
    pages,
    url: location.href,
    user_agent: navigator.userAgent,
    abstract,
    tags: Array.from(new Set(keywords.filter(Boolean))).slice(0, 8),
    pdf_url: cleanPdf[0] || "",
    pdf_candidates: cleanPdf,
    site: scienceDirect ? "ScienceDirect" : siteName,
  };
}

async function probeBase(base, timeout = 700) {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    const response = await fetch(base + "/api/health", { signal: controller.signal });
    clearTimeout(timer);
    if (!response.ok) return false;
    const data = await response.json();
    return Boolean(data && ["PaperWhale", "NPL", "Nexus Paper Library"].some(name => (data.app || "").includes(name)));
  } catch (err) {
    return false;
  }
}

async function findBase() {
  const stored = await chrome.storage.local.get(["port", "base"]);
  const bases = [];
  if (stored.base) bases.push(stored.base);
  if (stored.port) bases.push(`http://127.0.0.1:${stored.port}`);
  DEFAULT_PORTS.forEach((port) => {
    const base = `http://127.0.0.1:${port}`;
    if (!bases.includes(base)) bases.push(base);
  });
  // 并行探测，避免 8765 被占用时逐个端口等到天荒地老
  const checks = await Promise.all(
    bases.map(async (base) => ((await probeBase(base)) ? base : "")),
  );
  const found = checks.find(Boolean) || "";
  if (found) {
    state.base = found;
    const port = Number(found.split(":").pop());
    await chrome.storage.local.set({ port, base: found });
  }
  return found;
}

function bytesToBase64(bytes) {
  let binary = "";
  const chunk = 0x8000;
  for (let index = 0; index < bytes.length; index += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(index, index + chunk));
  }
  return btoa(binary);
}

async function fetchPdfBase64(url, deadline, diagnostics=false) {
  return fetchPdfInPage(url, deadline, "", diagnostics);
}

/* Self-contained so the same bounded downloader can run in the page context. */
async function fetchPdfInPage(url, deadline=Date.now()+60000, expectedUrl="", diagnostics=false) {
  const attempts=[];
  const record=(target,code,status)=>{
    let safe='';try{const parsed=new URL(target);if(['http:','https:'].includes(parsed.protocol)){parsed.username='';parsed.password='';parsed.search='';parsed.hash='';safe=parsed.href;}}catch{}
    if(attempts.length<24)attempts.push({url:safe,code,...(Number.isInteger(status)?{status}:{})});
  };
  const finish=(base64,blocked=false)=>diagnostics?{base64,attempts,...(blocked?{blocked:true}:{})}:base64;
  if(expectedUrl&&location.href!==expectedUrl){record(url,'network_error');return finish('');}
  if(Date.now()>=deadline){record(url,'timeout');return finish('');}
  if (!url) return finish('');
  const controller=new AbortController();
  const timer=setTimeout(() => controller.abort(),Math.max(1,deadline-Date.now()));
  const limit=80*1024*1024;
  const visited=new Set();
  async function resolve(target,depth=0) {
    if(Date.now()>=deadline){record(target,'timeout');return '';}
    if(depth>3||visited.size>=8||visited.has(target)){record(target,'redirect_limit');return '';}
    if(!/^https?:\/\//i.test(target)){record(target,'network_error');return '';}
    visited.add(target);
    try {
    const response=await fetch(target,{credentials:"include",cache:"no-store",signal:controller.signal});
    if(!response.ok){record(target,response.status===403?'access_denied':response.status===429?'rate_limited':response.status>=500?'server_error':'http_error',response.status);return '';}
    if(Number(response.headers.get('content-length')||0)>limit){record(target,'too_large');return '';}
    if(!response.body){record(target,'not_pdf');return '';}
    const reader=response.body.getReader(),chunks=[];
    let total=0,first='';
    while(true) {
      const {done,value}=await reader.read();if(done)break;
      total+=value.byteLength;
      if(first.length<1024)first+=new TextDecoder('latin1').decode(value.subarray(0,1024-first.length));
      if(total>limit||(!first.includes('%PDF-')&&total>2*1024*1024)){record(target,total>limit?'too_large':'not_pdf');await reader.cancel();return '';}
      chunks.push(value);
    }
    const bytes=new Uint8Array(total);let offset=0;
    for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.length;}
    if(new TextDecoder('latin1').decode(bytes.subarray(0,1024)).includes('%PDF-')) {
      if(total<1024){record(target,'not_pdf');return '';}
      let binary='';for(let index=0;index<bytes.length;index+=0x8000)binary+=String.fromCharCode.apply(null,bytes.subarray(index,index+0x8000));
      record(target,'ok');
      return btoa(binary);
    }
    record(target,'not_pdf');
    const html=new TextDecoder().decode(bytes);
    // 出版社的安全验证/下载准备页：继续换候选只会多打请求、还会让浏览器下回 init.htm，
    // 所以在这里就停手，把“需要人过一次验证”如实告诉用户。
    if(/Security verification|Preparing your download|problem was encountered, with your browser/i.test(html))return finish('',true);
    const base=response.url||target,doc=new DOMParser().parseFromString(html,'text/html'),candidates=[];
    const add=value=>{try{const next=new URL(value,base);if(value&&value!=='#'&&['http:','https:'].includes(next.protocol)&&!candidates.includes(next.href))candidates.push(next.href);}catch{}};
    doc.querySelectorAll('meta[http-equiv]').forEach(node=>{if(node.getAttribute('http-equiv').toLowerCase()==='refresh'){const match=(node.getAttribute('content')||'').match(/^\s*\d+(?:\.\d+)?\s*;\s*url\s*=\s*(.+)$/i);if(match)add(match[1].trim().replace(/^["']|["']$/g,''));}});
    doc.querySelectorAll('#redirect-message a[href], a#pdfLink[href], .pdf-download-btn-link[href], .PdfDropDownMenu a[href], meta[name="citation_pdf_url"], embed[src], object[data], a[href*=".pdf"], a[href*="/pdfft"], a[href*="md5="], a[href*="download=true"], link[type="application/pdf"]').forEach(node=>add(node.getAttribute('href')||node.getAttribute('content')||node.getAttribute('src')||node.getAttribute('data')));
    doc.querySelectorAll('script[type="application/json"]').forEach(node=>{
      try {
        const walk=value=>{
          if(!value||typeof value!=='object')return;
          if(Array.isArray(value)){value.forEach(walk);return;}
          const info=value.pdfDownload&&value.pdfDownload.urlMetadata;
          if(info&&typeof info==='object'){
            try{
              const path=String(info.path||'').replace(/^\/+|\/+$/g,'');
              if(/^[\w/-]+$/.test(path)&&/^[A-Z0-9]+$/.test(String(info.pii||''))&&['/pdfft','.pdf'].includes(info.pdfExtension)&&info.queryParams&&typeof info.queryParams.md5==='string'&&typeof info.queryParams.pid==='string'){
                add('/'+path+'/'+info.pii+info.pdfExtension+'?'+new URLSearchParams(info.queryParams));
              }
            }catch{}
          }
          Object.keys(value).forEach(key=>walk(value[key]));
        };
        walk(JSON.parse(node.textContent));
      } catch {}
    });
    for(const candidate of candidates.slice(0,8)){const result=await resolve(candidate,depth+1);if(result)return result;}
    return '';
    } catch {
      record(target,controller.signal.aborted||Date.now()>=deadline?'timeout':'network_error');
      return '';
    }
  }
  try {
    return finish(await resolve(url));
  } catch {
    record(url,controller.signal.aborted||Date.now()>=deadline?'timeout':'network_error');
    return finish('');
  } finally {
    clearTimeout(timer);
  }
}

async function cookieHeader(url) {
  if (!url || !chrome.cookies) return "";
  try {
    const list = await chrome.cookies.getAll({ url });
    return (list || []).map((cookie) => `${cookie.name}=${cookie.value}`).join("; ");
  } catch (err) {
    return "";
  }
}

function safeAttemptUrl(url) {
  try {const parsed=new URL(url);if(!['http:','https:'].includes(parsed.protocol))return '';parsed.username='';parsed.password='';parsed.search='';parsed.hash='';return parsed.href;}catch{return '';}
}

function pdfEvidenceUrl(details) {
  const headers = details.responseHeaders || [];
  const header = (name) => (headers.find((item) => String(item.name || '').toLowerCase() === name)?.value || '').toLowerCase();
  const contentType = header('content-type');
  const disposition = header('content-disposition');
  const attachmentPdf = disposition.includes('attachment') && /\.pdf(?:["';]|$)/i.test(disposition);
  if (contentType.includes('application/pdf') || contentType.includes('application/epub') || attachmentPdf) return details.url;
  // 只认路径结尾的 .pdf；查询串里的 pid=1-s2.0-XXXX-main.pdf 只是出版社的参数，
  // 按它放行会把“Preparing your download”这类 HTML 中间页当 PDF 强制下载（曾存成 pdfft.htm）。
  try {
    if (/\.pdf$/i.test(new URL(details.url).pathname)) return details.url;
  } catch {
    /* 无法解析的地址不算证据 */
  }
  return '';
}

function waitForPdfResponse(tabId, timeout, { subresources = false, onHtmlAttachment } = {}) {
  return new Promise((resolve) => {
    let done = false;
    const cleanup = () => {
      if (done) return;
      done = true;
      try { chrome.webRequest.onHeadersReceived.removeListener(onHeaders); } catch {}
      try { chrome.tabs.onUpdated.removeListener(onUpdated); } catch {}
      clearTimeout(timer);
      resolve(found || '');
    };
    let found = '';
    const onHeaders = (details) => {
      if (details.tabId !== tabId || !types.includes(details.type)) return;
      const url = pdfEvidenceUrl(details);
      if (url) {
        found = url;
        cleanup();
        return;
      }
      // 出版社常把“准备下载/安全验证”的 HTML 用 Content-Disposition: attachment 发下来，
      // 浏览器会把它存成 init.htm 之类的文件。早停并如实上报，别静默空手。
      const headers = details.responseHeaders || [];
      const disposition = (headers.find((item) => String(item.name || '').toLowerCase() === 'content-disposition')?.value || '').toLowerCase();
      const attachment = disposition.includes('attachment');
      const htmlFile = /\.(?:html?|xhtml|txt)(?:["';]|$)/i.test(disposition);
      if (attachment && htmlFile) {
        try { onHtmlAttachment?.(details.url); } catch {}
        cleanup();
      }
    };
    const onUpdated = (updatedTabId, info) => {
      if (updatedTabId !== tabId) return;
      const url = info.url || '';
      let pathPdf = false;
      try { pathPdf = /\.pdf$/i.test(new URL(url).pathname); } catch {}
      if (pathPdf || /pdf\.sciencedirectassets\.com/i.test(url)) {
        found = url;
        cleanup();
      }
    };
    const types = subresources ? ['main_frame', 'sub_frame', 'xmlhttprequest', 'other'] : ['main_frame', 'sub_frame'];
    const timer = setTimeout(cleanup, timeout);
    try {
      chrome.webRequest.onHeadersReceived.addListener(
        onHeaders,
        { urls: ['<all_urls>'], tabId, types },
        ['responseHeaders'],
      );
      chrome.tabs.onUpdated.addListener(onUpdated);
    } catch {
      cleanup();
    }
  });
}

// 等一个 Promise，但到点就返回 null；关键是输家定时器要被清掉，否则 popup 关掉后
// 还会挂着十几秒的 timer（测试里表现为进程迟迟不退出）。
function raceTimeout(promise, timeout) {
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(null), timeout);
    Promise.resolve(promise).then(
      (value) => { clearTimeout(timer); resolve(value); },
      () => { clearTimeout(timer); resolve(null); },
    );
  });
}

function waitForCreatedDownload(timeout) {  return new Promise((resolve) => {
    let done = false;
    const cleanup = () => {
      if (done) return;
      done = true;
      try { chrome.downloads.onCreated.removeListener(onCreated); } catch {}
      clearTimeout(timer);
    };
    const onCreated = (item) => {
      if (!item || !item.id) return;
      cleanup();
      resolve(item);
    };
    const timer = setTimeout(() => { cleanup(); resolve(null); }, timeout);
    try { chrome.downloads.onCreated.addListener(onCreated); } catch { cleanup(); resolve(null); }
  });
}

function waitForDownload(downloadId, timeout) {
  return new Promise((resolve) => {
    const started = Date.now();
    const tick = async () => {
      try {
        const [item] = await chrome.downloads.search({ id: downloadId });
        if (!item) return resolve(null);
        if (item.state === 'complete') return resolve(item);
        if (item.state === 'interrupted') return resolve(null);
      } catch {
        return resolve(null);
      }
      if (Date.now() - started > timeout) return resolve(null);
      setTimeout(tick, 300);
    };
    tick();
  });
}

async function capturePdfViaHiddenIframe(urls, sourceTab, deadline) {
  if (!sourceTab?.id) return '';
  for (const candidate of (urls || []).slice(0, 3)) {
    if (!/^https?:/i.test(candidate) || Date.now() >= deadline) continue;
    // ScienceDirect 对没有来源页的隐藏 iframe 直接回“Preparing your download”HTML，
    // 硬下载只会得到 pdfft.htm；这类站点只走真实标签页导航。
    if (/sciencedirect\.com/i.test(candidate)) continue;
    const frameId = 'pw-pdf-frame-' + Math.random().toString(36).slice(2, 9);
    const waitPromise = waitForPdfResponse(sourceTab.id, Math.min(14000, Math.max(3000, deadline - Date.now())));
    try {
      await chrome.scripting.executeScript({
        target: { tabId: sourceTab.id },
        func: (url, id) => {
          const frame = document.createElement('iframe');
          frame.id = id;
          frame.style.cssText = 'display:none;width:0;height:0;border:0';
          frame.src = url;
          document.body.appendChild(frame);
        },
        args: [candidate, frameId],
      });
      const found = await waitPromise;
      if (!found) continue;
      const downloadId = await chrome.downloads.download({ url: found, conflictAction: 'uniquify', saveAs: false });
      const item = await waitForDownload(downloadId, Math.min(30000, Math.max(4000, deadline - Date.now())));
      if (item && item.filename) return item.filename;
    } catch {
      /* 尝试下一个候选 */
    } finally {
      try {
        await chrome.scripting.executeScript({
          target: { tabId: sourceTab.id },
          func: (id) => document.getElementById(id)?.remove(),
          args: [frameId],
        });
      } catch {}
    }
  }
  return '';
}

// 非 .pdf 的下载（init.htm / pdfft.htm 这类中间页）不能留在用户下载目录里。
async function discardDownload(item) {
  if (!item || !item.id) return;
  try { chrome.downloads.cancel(item.id); } catch {}
  try { chrome.downloads.removeFile(item.id); } catch {}
  try { chrome.downloads.erase({ id: item.id }); } catch {}
}

async function capturePdfViaTab(urls, deadline, attempts) {
  // 探测失败也要留下证据：只记服务端已认识的 code，route 固定 browser-extension。
  const note = (url, code = 'not_pdf') => {
    if (!Array.isArray(attempts) || attempts.length >= 24) return;
    attempts.push({ url: safeAttemptUrl(url), code, route: 'browser-extension' });
  };
  for (const candidate of (urls || []).slice(0, 3)) {
    if (!/^https?:/i.test(candidate) || Date.now() >= deadline) continue;
    let tabId = null;
    const htmlAttachments = [];
    try {
      const createdPromise = waitForCreatedDownload(Math.min(15000, Math.max(3000, deadline - Date.now())));
      // 只在后台标签页里探测，探测完立刻关掉：用户当前的文章页必须留在原处，不能跟着跳走。
      const tab = await chrome.tabs.create({ url: candidate, active: false });
      tabId = tab.id;
      const found = await waitForPdfResponse(tabId, Math.min(12000, Math.max(2500, deadline - Date.now())), {
        subresources: true,
        onHtmlAttachment: (url) => htmlAttachments.push(url),
      });
      for (const url of htmlAttachments) note(url);
      if (htmlAttachments.length) continue;
      if (found) {
        const downloadId = await chrome.downloads.download({
          url: found,
          conflictAction: 'uniquify',
          saveAs: false,
        });
        const item = await waitForDownload(downloadId, Math.min(30000, Math.max(4000, deadline - Date.now())));
        if (item && item.filename) {
          if (/\.pdf$/i.test(item.filename)) return item.filename;
          // 嗅探到的“PDF”实际落成网页文件：留证据、删文件，别污染下载目录，也别再白等竞速。
          note(found);
          await discardDownload(item);
          continue;
        }
      }
      const created = await raceTimeout(createdPromise, Math.max(2500, Math.min(12000, deadline - Date.now())));
      if (created) {
        if (/\.pdf$/i.test(created.filename || '')) return created.filename;
        note(created.url || candidate);
        await discardDownload(created);
      }
      continue;
    } catch {
      /* 尝试下一个候选 */
    } finally {
      if (tabId !== null) {
        try { await chrome.tabs.remove(tabId); } catch {}
      }
    }
  }
  return '';
}

async function grabPdfBase64(url, deadline=Date.now()+90000, tab=state.tab, attempts) {
  const collect=(result,route)=>{
    const codes=new Set(['ok','access_denied','rate_limited','server_error','http_error','timeout','network_error','not_pdf','too_large','redirect_limit']);
    if(Array.isArray(attempts))for(const item of (result?.attempts||[]).slice(0,24))if(codes.has(item.code)&&attempts.length<24)attempts.push({url:safeAttemptUrl(item.url),code:item.code,route,...(Number.isInteger(item.status)?{status:item.status}:{})});
    if(result&&typeof result==='object'&&result.blocked)state.pdfBlocked=true;
    // 这里只允许返回字符串：诊断对象（含 attempts/blocked）一旦漏进 payload.pdf_base64，
    // 服务端 WebImportIn 会 422（Input should be a valid string）。
    const value=typeof result==='string'?result:result?.base64;
    return typeof value==='string'?value:'';
  };
  const failure=route=>collect({attempts:[{url,code:Date.now()>=deadline?'timeout':'network_error'}]},route);
  if(Date.now()>=deadline){failure('browser-extension');return '';}
  if(!tab?.id)return collect(await fetchPdfBase64(url,deadline,true),'browser-extension');
  const check=async()=>{const current=await chrome.tabs.get(tab.id);if(current.url!==tab.url)throw new Error('Page changed; reopen connector.');};
  await check();
  let base64='';
  try {
    const injection=await chrome.scripting.executeScript({target:{tabId:tab.id},func:fetchPdfInPage,args:[url,Math.min(deadline,Date.now()+15000),tab.url,true]});
    const result=injection?.[0]?.result;
    base64=collect(result,'browser-page');
    if(!base64&&!result?.attempts?.length)failure('browser-page');
  } catch {failure('browser-page');}
  await check();
  return base64 || collect(await fetchPdfBase64(url,Math.min(deadline,Date.now()+10000),true),'browser-extension');
}

function escapeHtml(value) {
  return String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
}

function pdfAttemptSummary(attempts) {
  const labels={access_denied:'服务器拒绝访问（HTTP 403）',rate_limited:'请求过于频繁（HTTP 429）',server_error:'服务器错误',http_error:'HTTP 请求失败',timeout:'请求超时',network_error:'网络请求失败（浏览器未提供原因，可能与网络或跨域限制有关）',not_pdf:'返回网页或不完整 PDF',too_large:'文件超过 80 MB 限制',redirect_limit:'页面跳转次数达到限制'};
  return (attempts||[]).filter(item=>labels[item.code]).slice(0,24).map(item=>`${item.route==='browser-page'?'网页':'扩展'}：${labels[item.code]} ${safeAttemptUrl(item.url)}`).join('；');
}

function fillForm(meta) {
  $("f-title").value = meta.title || "";
  $("f-authors").value = (meta.authors || []).join("; ");
  $("f-year").value = meta.year || "";
  $("f-venue").value = meta.venue || "";
  $("f-doi").value = meta.doi || "";
  $("f-arxiv").value = meta.arxiv_id || "";
  $("f-pdf").value = meta.pdf_url || "";
  $("f-abstract").value = meta.abstract || "";
  syncTitleBox(meta.title || "");
  const hint = $("page-hint");
  const site = meta.site && typeof meta.site === "object" ? String(meta.site.name || meta.site.site || "") : String(meta.site || "");
  hint.textContent = site ? `来源：${site}` : "";
}

/* 弹窗只露一个论文名：标题框是 contenteditable，改动同步回隐藏的 f-title。 */
function syncTitleBox(title) {
  const box = $("paper-title");
  if (box) box.textContent = title || "";
}

/* 用户自己选：直接归档 / AI 精读后归档。没选就不让保存。 */
function chosenSaveMode() {
  if (typeof document.querySelectorAll !== "function") return "archive";
  const picked = Array.from(document.querySelectorAll('input[name="save-mode"]')).find((node) => node.checked);
  return picked ? picked.value : "";
}

/* 直接归档时要让用户自己挑归档路径：从本机程序的分类树里取已有路径。 */
async function loadArchivePaths() {
  const select = $("archive-path");
  if (!select || select.dataset?.loaded === "1") return;
  try {
    if (!state.base) state.base = await findBase();
    if (!state.base) return;
    const response = await fetch(state.base + "/api/categories/tree");
    if (!response.ok) return;
    const data = await response.json().catch(() => ({}));
    const paths = Array.isArray(data.paths) ? data.paths : [];
    const options = ['<option value="">未分类（默认目录）</option>'];
    for (const raw of paths.slice(0, 500)) {
      const label = String(raw || "").trim();
      if (!label) continue;
      options.push(`<option value="${escapeHtml(label)}">${escapeHtml(label)}</option>`);
    }
    select.innerHTML = options.join("");
    if (select.dataset) select.dataset.loaded = "1";
  } catch {
    /* 拿不到分类就用默认目录，不影响保存 */
  }
}

function toggleArchivePath() {
  const wrap = $("archive-path-wrap");
  const nodes = typeof document.querySelectorAll === "function" ? Array.from(document.querySelectorAll('input[name="save-mode"]')) : [];
  const archive = nodes.some((node) => node.checked && node.value === "archive");
  if (wrap) wrap.classList.toggle("hidden", !archive);
  if (archive) loadArchivePaths();
}

function readForm() {
  return {
    title: $("f-title").value.trim(),
    authors: $("f-authors")
      .value.split(/[;；]/)
      .map((item) => item.trim())
      .filter(Boolean),
    year: $("f-year").value.trim(),
    venue: $("f-venue").value.trim(),
    doi: $("f-doi").value.trim(),
    arxiv_id: $("f-arxiv").value.trim(),
    pdf_url: $("f-pdf").value.trim(),
    abstract: $("f-abstract").value.trim(),
    download_pdf: $("opt-pdf").checked,
    analyze: $("opt-analyze").checked,
  };
}

/* ---------- Google Scholar 搜索结果：勾选多条批量入库 ---------- */
function isScholarUrl(url) {
  return /^https?:\/\/(?:www\.)?scholar\.google\.[a-z.]{2,}\//i.test(String(url || ""));
}

/* 在页面里执行：把 Scholar 结果页的条目读成结构化数据。
   注意这个函数会被 chrome.scripting.executeScript 序列化注入，必须自包含。 */
function extractScholarResults(limit) {
  const max = Math.max(1, Math.min(50, Number(limit) || 20));
  const rows = Array.from(document.querySelectorAll('#gs_res_ccl_mid .gs_r, #gs_res_ccl_mid [data-cid], .gs_r.gs_or.gs_scl')).slice(0, max * 2);
  const items = [];
  const seen = new Set();
  for (const row of rows) {
    if (items.length >= max) break;
    const heading = row.querySelector('h3.gs_rt') || row.querySelector('h3');
    if (!heading) continue;
    const title = String(heading.textContent || '').replace(/^\s*\[(PDF|HTML|BOOK|CITATION|B)\]\s*/i, '').replace(/\s+/g, ' ').trim();
    if (!title || seen.has(title)) continue;
    seen.add(title);
    const link = heading.querySelector('a');
    const metaNode = row.querySelector('.gs_a');
    const meta = String((metaNode && metaNode.textContent) || '').replace(/\s+/g, ' ').trim();
    const parts = meta.split(' - ').map((part) => part.trim());
    const authors = parts[0] || '';
    const yearMatch = meta.match(/(?:19|20)\d{2}/);
    const year = yearMatch ? yearMatch[0] : '';
    let venue = parts[1] || '';
    venue = venue.replace(/,\s*(?:19|20)\d{2}[\s\S]*$/, '').replace(/(?:19|20)\d{2}[\s\S]*$/, '').replace(/[,\s]+$/, '').trim();
    const pdfLink = row.querySelector('.gs_or_ggsm a, a.gs_or_ggsm, .gs_ggsm a');
    items.push({
      title,
      url: (link && link.href) || '',
      authors: authors === '…' ? '' : authors,
      venue,
      year,
      pdf_url: (pdfLink && pdfLink.href) || '',
    });
  }
  return items;
}

const scholarState = { results: [], selected: new Set(), busy: false };

function renderScholarRows() {
  const list = $("scholar-list");
  if (!list) return;
  list.innerHTML = scholarState.results.map((item, index) => {
    const checked = scholarState.selected.has(index) ? ' checked' : '';
    const meta = [item.authors, item.venue, item.year].filter(Boolean).join(' · ');
    const kind = item.pdf_url ? '有 PDF 直链' : '仅条目';
    return `<label class="scholar-row"><input type="checkbox" data-index="${index}"${checked} />`
      + `<span class="scholar-row-body"><b>${escapeHtml(item.title)}</b>`
      + `<small>${escapeHtml(meta || '（无作者信息）')} · ${kind}</small></span></label>`;
  }).join("");
  const count = $("scholar-count");
  if (count) count.textContent = `${scholarState.results.length} 条 · 已选 ${scholarState.selected.size}`;
  const button = $("scholar-import");
  if (button) {
    button.disabled = scholarState.busy || scholarState.selected.size === 0;
    button.textContent = scholarState.selected.size ? `批量入库（${scholarState.selected.size}）` : "批量入库";
  }
}

function bindScholarPanel() {
  const list = $("scholar-list");
  if (list && !list.__pwScholarBound) {
    list.__pwScholarBound = true;
    list.addEventListener("change", (event) => {
      const raw = event && event.target && event.target.dataset ? event.target.dataset.index : undefined;
      const index = Number(raw);
      if (!Number.isInteger(index) || index < 0 || index >= scholarState.results.length) return;
      const checked = event.target.checked !== undefined ? Boolean(event.target.checked) : scholarState.selected.has(index);
      if (checked) scholarState.selected.add(index);
      else scholarState.selected.delete(index);
      renderScholarRows();
    });
  }
  const selectAll = $("scholar-select-all");
  if (selectAll && !selectAll.__pwScholarBound) {
    selectAll.__pwScholarBound = true;
    selectAll.addEventListener("click", () => {
      scholarState.selected = new Set(scholarState.results.map((_, index) => index));
      renderScholarRows();
    });
  }
  const selectNone = $("scholar-select-none");
  if (selectNone && !selectNone.__pwScholarBound) {
    selectNone.__pwScholarBound = true;
    selectNone.addEventListener("click", () => {
      scholarState.selected = new Set();
      renderScholarRows();
    });
  }
  const importButton = $("scholar-import");
  if (importButton && !importButton.__pwScholarBound) {
    importButton.__pwScholarBound = true;
    importButton.addEventListener("click", () => { importScholar(); });
  }
}

async function initScholar(tab) {
  const panel = $("scholar-panel");
  if (panel) panel.classList.remove("hidden");
  $("form")?.classList.add("hidden");
  bindScholarPanel();
  setConn("正在读取 Google Scholar 结果…");
  const status = $("scholar-status");
  try {
    const [base, injection] = await Promise.all([
      findBase(),
      chrome.scripting.executeScript({ target: { tabId: tab.id }, func: extractScholarResults, args: [20] }),
    ]);
    state.base = base || state.base;
    const results = (injection && injection[0] && injection[0].result) || [];
    scholarState.results = Array.isArray(results) ? results.filter((item) => item && item.title).slice(0, 20) : [];
    scholarState.selected = new Set(scholarState.results.map((_, index) => index));
    renderScholarRows();
    if (base) setConn(`已连接 ${base}`, "ok");
    else setConn("没连上 NPL：请先启动程序，批量入库时会自动重试", "bad");
    if (!scholarState.results.length && status) {
      status.textContent = "这一页没解析到结果。如果是验证页或单篇详情页，请回到搜索结果页再打开插件。";
    }
  } catch (err) {
    setConn("读取 Google Scholar 失败", "bad");
    showError(formatError(err));
  }
}

async function importScholar() {
  if (scholarState.busy) return;
  const status = $("scholar-status");
  const chosen = scholarState.results.filter((_, index) => scholarState.selected.has(index));
  if (!chosen.length) {
    if (status) status.textContent = "先勾选要入库的条目";
    return;
  }
  if (!state.base) state.base = await findBase();
  if (!state.base) {
    showError("没连上 NPL，请先启动程序再试。");
    return;
  }
  scholarState.busy = true;
  renderScholarRows();
  let added = 0;
  let duplicate = 0;
  let failed = 0;
  const ids = [];
  try {
    for (let index = 0; index < chosen.length; index += 1) {
      const item = chosen[index];
      if (status) status.textContent = `正在入库 ${index + 1}/${chosen.length}：${String(item.title).slice(0, 40)}`;
      try {
        const response = await fetch(state.base + "/api/import/web", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            title: item.title,
            authors: item.authors || "",
            year: item.year || "",
            venue: item.venue || "",
            url: item.url || item.pdf_url || "",
            item_type: "article",
            download_pdf: false,
          }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          failed += 1;
          continue;
        }
        if (data.duplicate) duplicate += 1;
        else added += 1;
        const id = data.paper && data.paper.id;
        if (id) ids.push(id);
      } catch {
        failed += 1;
      }
    }
    let queued = 0;
    if (ids.length) {
      try {
        const response = await fetch(state.base + "/api/pdf-fetch/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ paper_ids: ids }),
        });
        const data = await response.json().catch(() => ({}));
        queued = Number(data.queued) || 0;
      } catch {
        /* 排队失败不影响入库结果 */
      }
    }
    if (status) {
      status.textContent = `完成：新增 ${added} 篇，已存在 ${duplicate} 篇，失败 ${failed} 篇`
        + (queued ? `；已把 ${queued} 篇加入后台补 PDF 队列` : "");
    }
  } finally {
    scholarState.busy = false;
    renderScholarRows();
  }
}

async function init() {
  setConn("正在连接本机 NPL…");
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    state.tab = tab;
    if (!tab || !tab.id || /^(chrome|edge|about|chrome-extension):/i.test(tab.url || "")) {
      setConn("当前页面不能抓取，请打开论文页面", "bad");
      return;
    }
    if (isScholarUrl(tab.url)) {
      await initScholar(tab);
      return;
    }
    const [base, injection] = await Promise.all([
      findBase(),
      chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: extractPageMetadata,
      }),
    ]);
    state.meta = (injection && injection[0] && injection[0].result) || {};
    fillForm(state.meta);
    $("form").classList.remove("hidden");
    if (base) {
      setConn(`已连接 ${base}`, "ok");
    } else {
      setConn("没连上 NPL：请启动程序，保存时会自动重试", "bad");
    }
    const titleBox = $("paper-title");
    if (titleBox && !titleBox.__pwTitleBound) {
      titleBox.__pwTitleBound = true;
      titleBox.addEventListener("input", () => {
        $("f-title").value = (titleBox.textContent || "").trim();
      });
      if (!state.meta.title) titleBox.focus();
    }
    if (typeof document.querySelectorAll === "function") {
      document.querySelectorAll('input[name="save-mode"]').forEach((node) => node.addEventListener("change", toggleArchivePath));
      toggleArchivePath();
    }
  } catch (err) {
    setConn("连接失败", "bad");
    console.error(err);
    showError(formatError(err));
    $("form").classList.remove("hidden");
  }
}

async function save() {
  clearError();
  if (!state.base) {
    state.base = await findBase();
  }
  if (!state.base) {
    showError("没连上 NPL，请先启动程序再试。");
    return;
  }
  const payload = readForm();
  const saveMode = chosenSaveMode();
  if (!saveMode) {
    showError("先选一个：直接归档，还是 AI 精读后归档。");
    return;
  }
  payload.analyze = saveMode === "analyze";
  if (saveMode === "archive") {
    // 归档路径来自下拉框（分类路径），空值代表默认目录/未分类。
    const select = $("archive-path");
    const chosenPath = select && typeof select.value === "string" ? select.value.trim() : "";
    if (chosenPath) {
      payload.category_path = chosenPath.split(" / ").map((part) => part.trim()).filter(Boolean);
    }
  }
  payload.tags = Array.isArray(state.meta?.tags) ? state.meta.tags.filter((tag) => typeof tag === "string" && tag).slice(0, 8) : [];
  payload.item_type = "article";
  if (!payload.title) {
    showError("标题不能为空。");
    return;
  }
  const button = $("btn-save");
  button.disabled = true;
  button.textContent = "正在保存…";
  $("result").textContent = "";
  let step = "读取当前页面";
  let watchArmed = false;
  let dnrArmed = false;
  try {
    const tab=state.tab&&{...state.tab},deadline=Date.now()+90000;
    let live=state.meta||{};
    if(tab?.id) {
      const current=await chrome.tabs.get(tab.id);
      if(current.url!==tab.url)throw new Error('Page changed; reopen connector.');
      try {
        const injection=await chrome.scripting.executeScript({target:{tabId:tab.id},func:extractPageMetadata,args:[{expandPdfMenu:payload.download_pdf,expectedUrl:tab.url}]});
        const next=injection?.[0]?.result;
        if(next&&(next.title||next.doi||next.pdf_url||(next.pdf_candidates||[]).length))live=next;
      } catch (err) {
        const reason=formatError(err);
        if(/Page changed|Article changed/.test(reason)) throw err;
        // 二次读取页面失败时退回打开弹窗时抓到的元数据，不要因此阻断保存。
      }
      if((live.url&&live.url!==tab.url)||(state.meta?.doi&&live.doi&&state.meta.doi.toLowerCase()!==state.meta.doi.toLowerCase()))throw new Error('Article changed; reopen connector.');
    }
    payload.url=tab?.url||live.url||'';
    // 卷 / 期 / 页码：页面 meta 里抓到就带上（服务端会做归一化），抓不到就留空。
    payload.volume=live.volume||'';
    payload.issue=live.issue||'';
    payload.pages=live.pages||'';
    try {
      const htmlResult = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => document.documentElement.outerHTML,
      });
      payload.page_html = String(htmlResult?.[0]?.result || '').slice(0, 2_000_000);
    } catch {}
    if(payload.download_pdf) {
      step = "抓取 PDF";
      button.textContent='正在抓取 PDF…';payload.pdf_referer=payload.url;payload.pdf_user_agent=live.user_agent||'';
      // 出版社反盗链要求来源页；给扩展自己的请求临时补 Referer，成功抓到 PDF 后立刻撤规则。
      const refererRule = await installRefererRule(payload.url);
      dnrArmed = refererRule;
      try {
      payload.pdf_attempts=[];
      state.pdfBlocked=false;
      state.debuggerNote="";
      const manual=payload.pdf_url!==(state.meta?.pdf_url||'');
      const candidates=manual?[payload.pdf_url]:[...new Set([...(live.pdf_candidates||[]),live.pdf_url,...(state.meta?.pdf_candidates||[]),payload.pdf_url].filter(Boolean))];
      // 页面元数据来自任意网页：只把字符串 http(s) 地址交给抓取和上传，避免对象/数字混进 list[str]。
      const pdfCandidates=[...new Set(candidates.filter(url=>typeof url==='string'&&/^https?:/i.test(url)))].slice(0,8);
      payload.pdf_candidates=pdfCandidates;
      for(const candidate of pdfCandidates.slice(0,4)) {
        if(Date.now()>=deadline)break;
        payload.pdf_url=candidate.replace('stamp/stamp.jsp','stampPDF/getPDF.jsp');
        const base64=await grabPdfBase64(payload.pdf_url,deadline,tab,payload.pdf_attempts);
        if(typeof base64==='string'&&base64){payload.pdf_base64=base64;payload.pdf_name=(payload.title||'paper').slice(0,60)+'.pdf';break;}
        if(state.pdfBlocked)break;
      }
      // 调试通道优先：它在真机上被证明能拿到出版社只肯给"真实导航"的 PDF 正文，
      // 而且不会像标签页探测那样先落一个 init.htm 到用户下载目录。
      if(!payload.pdf_base64 && payload.download_pdf) {
        button.textContent='正在用浏览器调试通道抓取 PDF…';
        const grabbed = await capturePdfViaDebugger(pdfCandidates, deadline, payload.pdf_attempts, payload.url);
        if(grabbed){payload.pdf_base64=grabbed;payload.pdf_name=(payload.title||'paper').slice(0,60)+'.pdf';}
      }
      if(!payload.pdf_base64 && payload.download_pdf && !state.pdfBlocked) {
        button.textContent='正在通过已验证页面抓取 PDF…';
        let localPath=await capturePdfViaHiddenIframe(pdfCandidates, tab, deadline);
        if(!localPath) localPath=await capturePdfViaTab(pdfCandidates, deadline, payload.pdf_attempts);
        if(localPath){payload.pdf_download_path=localPath;payload.pdf_name=(payload.title||'paper').slice(0,60)+'.pdf';}
      }
      if(!payload.pdf_base64 && !payload.pdf_download_path && payload.pdf_url && !state.pdfBlocked)payload.pdf_cookies=await cookieHeader(payload.pdf_url);
      // 自动抓不到（常见于 ScienceDirect）：直接把“接住手动下载”挂上，
      // 用户只要在网页上点一次 Download PDF，下载完成就自动入库，不用再回弹窗点第二次。
      if(!payload.pdf_base64 && !payload.pdf_download_path && payload.title){
        watchArmed = await armWatch(state.base, payload, payload.url);
      }
      } finally {
        if (refererRule) await removeRefererRule();
      }
    }
    if(tab?.id && (await chrome.tabs.get(tab.id)).url!==tab.url)throw new Error("Page changed; reopen connector.");
    step = "提交到 NPL";
    button.textContent = "正在保存…";
    const response = await fetch(state.base + "/api/import/web", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = data && data.detail !== undefined ? data.detail : '';
      let text;
      try {
        text = typeof detail === 'string' ? detail : JSON.stringify(detail);
      } catch {
        text = String(detail);
      }
      throw new Error(`HTTP ${response.status}：${(text || '未知原因').slice(0, 400)}`);
    }
    const paper = data.paper || {};
    // 服务端没把 PDF 收下（例如抓到的是伪装成 .pdf 的网页、被判 invalid_pdf）时，
    // 也把「接住手动下载」挂上，避免用户手里明明有 PDF 却要再来一次保存。
    if (!payload.pdf_download_path && data.pdf !== "attached" && !watchArmed && payload.title) {
      watchArmed = await armWatch(state.base, payload, payload.url);
    }
    if (data.pdf === "attached") {
      // PDF 已经入库，清掉之前残留的 watch/失败记录，避免下次开弹窗看到过期的“接住失败”。
      try { await chrome.storage.local.remove(["pwWatch", "pwWatchResult"]); } catch {}
    }
    const lines = [];
    lines.push(
      data.duplicate
        ? `<div class="ok">✓ 这篇已经在库里，已更新元数据</div>`
        : `<div class="ok">✓ 已保存到 NPL</div>`,
    );
    lines.push(`<div>${escapeHtml((paper.title || payload.title).slice(0, 90))}</div>`);
    if (data.pdf === "attached") lines.push(`<div>PDF 已下载并归档</div>`);
    else if (data.pdf === "queued") lines.push(`<div class="warn">PDF 暂未直链，后台正在尝试抓取</div>`);
    else lines.push(`<div class="warn">只保存了网页信息，暂无 PDF</div>`);
    if(data.pdf!=="attached") {
      const summary=pdfAttemptSummary(payload.pdf_attempts);
      if(summary)lines.push(`<div class="warn">浏览器抓取记录：${escapeHtml(summary)}</div>`);
    }
    if(state.debuggerNote) lines.push(`<div class="warn">调试通道：${escapeHtml(state.debuggerNote)}</div>`);
    if(state.pdfBlocked) lines.push(`<div class="warn">出版社要求安全验证：请先在浏览器里打开这篇论文页完成验证，或用页面上的 Download PDF 手动下载后点「接住手动下载的 PDF」。</div>`);
    if(payload.download_pdf && !dnrArmed) lines.push(`<div class="warn">浏览器没给「修改网络请求」权限：请到 edge://extensions 重新加载 NPL Connector 后再试，否则拿不到需要来源页的 PDF。</div>`);
    if(payload.download_pdf && dnrArmed) lines.push(`<div class="hint">已临时启用请求来源（Referer）规则抓取</div>`);
    if(watchArmed) lines.push(`<div class="warn">没抓到 PDF，已自动开始监听：现在到网页上点一次 Download PDF（或先点 View PDF 再下载），文件会自动入库。</div>`);
    for(const warning of [data.pdf_warning,data.identity_warning]) {
      if(typeof warning==='string'&&warning)lines.push(`<div class="warn">${escapeHtml(warning)}</div>`);
    }
    if (payload.analyze && data.pdf !== "attached") {
      lines.push(`<div class="warn">没有 PDF，未触发精读</div>`);
    }
    $("result").innerHTML = lines.join("");
    state.saved = true;
  } catch (err) {
    console.error(err);
    showError(`[${step}] ${formatError(err)}`);
  } finally {
    button.disabled = false;
    button.textContent = "保存到 NPL";
  }
}

// 出版社的 PDF 常做反盗链：只认带来源页（Referer）的请求。扩展 fetch 默认不带 Referer，
// downloads.download 又不允许设置（Unsafe request header name）；实测唯一可行的是
// 按需安装一条 declarativeNetRequest 会话规则给扩展自己的请求补 Referer，用完立刻删。
const REFERER_RULE_ID = 9001;
async function installRefererRule(pageUrl) {
  try {
    if (!pageUrl || !/^https?:/i.test(pageUrl)) return false;
    if (!chrome.declarativeNetRequest || typeof chrome.declarativeNetRequest.updateSessionRules !== 'function') return false;
    await chrome.declarativeNetRequest.updateSessionRules({
      removeRuleIds: [REFERER_RULE_ID],
      addRules: [
        {
          id: REFERER_RULE_ID,
          priority: 1,
          action: {
            type: 'modifyHeaders',
            requestHeaders: [{ header: 'Referer', operation: 'set', value: pageUrl }],
          },
          condition: {
            requestDomains: ['sciencedirect.com', 'sciencedirectassets.com', 'elsevier.com'],
            resourceTypes: ['main_frame', 'sub_frame', 'xmlhttprequest', 'other'],
          },
        },
      ],
    });
    return true;
  } catch {
    return false;
  }
}

async function removeRefererRule() {
  try {
    if (!chrome.declarativeNetRequest || typeof chrome.declarativeNetRequest.updateSessionRules !== 'function') return;
    await chrome.declarativeNetRequest.updateSessionRules({ removeRuleIds: [REFERER_RULE_ID] });
  } catch {
    /* 规则已经不存在也无所谓 */
  }
}

// 最后一道兜底：出版社只肯把 PDF 交给"真实导航"，而 fetch/downloads 都拿不到字节。
// 用 chrome.debugger 挂后台标签页，走 CDP 的 Fetch 域在响应阶段直接读 PDF 正文
// （实测可行：Fetch.getResponseBody 能拿到 %PDF- 开头的内容）。读完立刻 detach + 关标签页。
/* 调试通道抓到 HTML 时的判断：是验证页、能跟着跳转、还是普通网页。
 * 出版社（Cloudflare/Akamai/Elsevier 自己的验证）常回 200 + text/html，
 * 以前只记一句「不是 PDF」，用户不知道要先去浏览器过验证。 */
const DEBUG_VERIFICATION_MARKERS = [
  'security verification', 'just a moment', 'checking your browser', 'cf-chl', 'captcha',
  'verify you are human', 'access denied', '请完成验证', '人机验证', '验证码',
  'enable javascript and cookies', 'unusual traffic',
];
function decodeBase64Head(base64, bytes = 4096) {
  try {
    return atob(String(base64 || '').slice(0, Math.ceil(bytes / 3) * 4));
  } catch {
    return '';
  }
}
function captureIsVerificationPage(text) {
  const lower = String(text || '').toLowerCase();
  return DEBUG_VERIFICATION_MARKERS.some((marker) => lower.includes(marker));
}
function captureHtmlRedirect(text, base) {
  const source = String(text || '');
  const patterns = [
    /location\.replace\(\s*['"]([^'"]+)['"]\s*\)/i,
    /location\.(?:href|assign)\s*=\s*['"]([^'"]+)['"]/i,
    /<meta[^>]+http-equiv=["']?refresh["']?[^>]*content=["'][^"']*url=([^"'>]+)/i,
  ];
  for (const pattern of patterns) {
    const match = source.match(pattern);
    if (!match) continue;
    try {
      const url = new URL(String(match[1]).replace(/&amp;/g, '&'), base || undefined).toString();
      if (/^https?:/i.test(url)) return url;
    } catch {
      /* 跳转地址不合法就忽略 */
    }
  }
  return '';
}

async function capturePdfViaDebugger(candidates, deadline, attempts, referrer) {
  const api = chrome.debugger;
  if (!api || typeof api.attach !== 'function' || typeof api.sendCommand !== 'function') return '';
  const note = (url, code = 'not_pdf') => {
    if (!Array.isArray(attempts) || attempts.length >= 24) return;
    attempts.push({ url: safeAttemptUrl(url), code, route: 'browser-extension' });
  };
  const ordered = (candidates || [])
    .filter((url) => typeof url === 'string' && /^https?:/i.test(url))
    .sort((a, b) => Number(/\/pdfft|\.pdf(?:[?#]|$)/i.test(b)) - Number(/\/pdfft|\.pdf(?:[?#]|$)/i.test(a)));
  const send = (target, method, params = {}) =>
    raceTimeout(new Promise((resolve, reject) => {
      api.sendCommand(target, method, params, (result) => {
        const err = chrome.runtime.lastError;
        if (err) reject(new Error(err.message));
        else resolve(result);
      });
    }), 10000).then((value) => {
      if (value === null) throw new Error('debugger command timeout');
      return value;
    });
  // 跟随 HTML 跳转页的次数上限（Elsevier 的 /pdfft 有时先回一个小跳转页）
  const queue = ordered.slice(0, 2);
  let redirectsLeft = 2;
  while (queue.length) {
    const candidate = queue.shift();
    if (Date.now() >= deadline) break;
    let stopAll = false;
    let redirectTarget = '';
    let tab = null;
    let attached = false;
    let detachListener = null;
    try {
      tab = await chrome.tabs.create({ url: 'about:blank', active: false });
      const target = { tabId: tab.id };
      const bodyPromise = new Promise((resolve) => {
        let settled = false;
        let bodyTimer = null;
        const finish = (value) => {
          if (settled) return;
          settled = true;
          if (bodyTimer) clearTimeout(bodyTimer);
          resolve(value);
        };
        const onEvent = async (source, method, params) => {
          if (!source || source.tabId !== tab.id || method !== 'Fetch.requestPaused') return;
          const url = (params.request && params.request.url) || '';
          const headers = params.responseHeaders || [];
          const contentType = String((headers.find((h) => String(h.name).toLowerCase() === 'content-type') || {}).value || '');
          let pdf = /pdf/i.test(contentType) || /\.pdf(?:[?#]|$)/i.test(url);
          if (!pdf) {
            try { pdf = /\.pdf$/i.test(new URL(url).pathname); } catch {}
          }
          // /pdfft 这类下载端点即使回 text/html 也要看一眼正文：有时是跳转页，能跟着跳到真 PDF。
          const inspect = pdf || /\/pdfft(?:[?#]|$)/i.test(url);
          if (inspect && params.responseStatusCode && !settled) {
            // 注意：这里不能先置 settled —— finish() 自己会置位并 clearTimeout，
            // 提前置位会让 finish() 直接返回，正文被丢掉、只能等兜底超时。
            try {
              const body = await send(target, 'Fetch.getResponseBody', { requestId: params.requestId });
              const base64 = body && body.body ? (body.base64Encoded ? body.body : btoa(body.body)) : '';
              // 只接受真正的 PDF：%PDF 开头的 base64 是 JVBER…。出版社常用 200+text/html
              // 或把 HTML 伪装成 .pdf 地址，不校验的话服务端会回 invalid_pdf。
              if (base64.startsWith('JVBER')) {
                state.debuggerNote = `已捕获 PDF 正文（${Math.round(base64.length * 0.75)} 字节）`;
                finish({ base64 });
              } else if (base64) {
                const bytes = Math.round(base64.length * 0.75);
                const text = decodeBase64Head(base64);
                if (captureIsVerificationPage(text)) {
                  note(url, 'needs_verification');
                  stopAll = true;
                  state.pdfBlocked = true;
                  state.debuggerNote = `出版社返回了安全验证页（${bytes} 字节），自动抓取已停止`;
                  finish(null);
                } else {
                  const redirect = captureHtmlRedirect(text, url);
                  if (redirect && redirectsLeft > 0) {
                    redirectsLeft -= 1;
                    redirectTarget = redirect;
                    note(url);
                    state.debuggerNote = `返回的是跳转页（${bytes} 字节），跟随跳转继续抓`;
                    finish(null);
                  } else {
                    state.debuggerNote = `捕获到的不是 PDF（${bytes} 字节，头部 ${base64.slice(0, 8)}），继续找下一个候选`;
                    note(url);
                  }
                }
              }
            } catch {
              /* 这条响应取不到正文，继续等其他响应或候选 */
            }
          }
          try { await send(target, 'Fetch.continueRequest', { requestId: params.requestId }); } catch {}
        };
        detachListener = onEvent;
        api.onEvent.addListener(onEvent);
        bodyTimer = setTimeout(() => finish(null), Math.min(20000, Math.max(3000, deadline - Date.now())));
      });
      const attachedResult = await raceTimeout(Promise.resolve(api.attach(target, '1.3')), 8000);
      if (attachedResult === null) throw new Error('debugger attach timeout');
      attached = true;
      await send(target, 'Fetch.enable', { patterns: [{ urlPattern: '*', requestStage: 'Response' }] });
      await send(target, 'Page.navigate', { url: candidate, ...(referrer ? { referrer } : {}) });
      const grabbed = await bodyPromise;
      if (grabbed && grabbed.base64) return grabbed.base64;
    } catch {
      /* 换下一个候选 */
    } finally {
      if (attached) { try { await raceTimeout(Promise.resolve(api.detach({ tabId: tab.id })), 5000); } catch {} }
      if (tab) { try { await chrome.tabs.remove(tab.id); } catch {} }
      try { if (detachListener) api.onEvent.removeListener(detachListener); } catch {}
    }
    if (stopAll) break;
    if (redirectTarget && !queue.includes(redirectTarget)) queue.unshift(redirectTarget);
  }
  if (!state.pdfBlocked) note(candidates && candidates[0]);
  return '';
}

async function armWatch(base, meta, pageUrl) {
  try {
    if (!chrome.runtime || typeof chrome.runtime.sendMessage !== "function") return false;
    const light = { ...meta };
    delete light.page_html;
    delete light.pdf_base64;
    delete light.pdf_cookies;
    await chrome.runtime.sendMessage({
      type: "pw-watch-download",
      watch: { base, meta: light, pageUrl, at: Date.now() },
    });
    return true;
  } catch {
    return false;
  }
}

async function startWatch() {
  if (!state.base) state.base = await findBase();
  if (!state.base) {
    showError("未连接 NPL，请先启动程序。");
    return;
  }
  const meta = readForm();
  meta.url = state.tab?.url || "";
  meta.tags = state.meta?.tags || [];
  meta.item_type = "article";
  await chrome.runtime.sendMessage({
    type: "pw-watch-download",
    watch: { base: state.base, meta, pageUrl: meta.url, at: Date.now() },
  });
  $("btn-watch").disabled = true;
  $("watch-hint").textContent = "已开始监听。现在请在网页上点“Download PDF”，PDF 下载完成后会自动入库。";
}

async function showWatchResult() {
  const stored = await chrome.storage.local.get(["pwWatchResult"]);
  const result = stored.pwWatchResult;
  if (!result) return;
  if (result.status === "ok") {
    $("watch-hint").textContent = `✓ 已接住 PDF 并入库：${result.title || ""}`;
  } else if (result.status === "saved_without_pdf") {
    $("watch-hint").textContent = "下载文件已接住，但没有识别成 PDF。";
  } else {
    $("watch-hint").textContent = `接住失败：${result.message || "未知原因"}`;
  }
}

async function hashSnippet(text) {
  try {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return Array.from(new Uint8Array(digest)).slice(0, 4).map((byte) => byte.toString(16).padStart(2, "0")).join("");
  } catch {
    return "";
  }
}

// 浏览器里跑的到底是哪份 popup.js：清单版本号 + 本文件前 8 位 SHA-256。
// 未打包扩展改了磁盘文件也必须手动“重新加载”，这个标识让版本对不上时一眼可见。
async function showBuildStamp() {
  const el = $("build-stamp");
  if (!el) return;
  let version = "?";
  try { version = chrome.runtime.getManifest().version || "?"; } catch { /* 非扩展环境 */ }
  let hash = "";
  try {
    const response = await fetch(chrome.runtime.getURL("popup.js"), { cache: "no-store" });
    hash = await hashSnippet(await response.text());
  } catch { /* 读不到自身文件时只显示清单版本 */ }
  el.textContent = `Connector v${version}${hash ? ` · popup.js ${hash}` : ""}`;
}

$("btn-save").addEventListener("click", save);
$("btn-watch").addEventListener("click", startWatch);
$("btn-options").addEventListener("click", () => chrome.runtime.openOptionsPage());
document.addEventListener("DOMContentLoaded", () => { init(); showWatchResult().catch(() => {}); showBuildStamp().catch(() => {}); });
