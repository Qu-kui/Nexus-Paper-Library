/* Shared keyboard and dialog behavior, including dynamically created content. */
export function initAccessibility() {
  const focusable = 'button:not(:disabled),a[href],input:not(:disabled):not([type="hidden"]),select:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"])';
  const opened = new Map();
  let lastFocus=document.activeElement;
  let readerOpener=null, readerOpen=false;
  const readerInert=new Map();
  function restoreReaderBackground() {
    for (const [node, inert] of readerInert) node.inert=inert;
    readerInert.clear();
  }
  function decorateReader() {
    const reader=document.getElementById('reader');
    const visible=reader && !reader.classList.contains('hidden');
    if(visible) {
      reader.setAttribute('role','dialog');reader.setAttribute('aria-modal','true');
      reader.setAttribute('aria-labelledby','reader-title');reader.tabIndex=-1;
      if(!readerOpen) {
        readerOpener=lastFocus;readerOpen=true;
        queueMicrotask(()=>{if(!reader.classList.contains('hidden') && !reader.contains(document.activeElement) && !document.activeElement?.closest('.modal:not(.hidden)')) (reader.querySelector('#reader-close')||reader).focus();});
      }
      // Keep separate modal roots available: the shared modal trap takes precedence.
      let branch=reader;
      while(branch.parentElement) {
        for(const sibling of branch.parentElement.children) {
          // Native window chrome remains usable while the document reader isolates its background.
          if(sibling===branch || sibling.matches('.modal,[aria-modal="true"],#app-titlebar,.tb-resize,#clipboard-prompt') || sibling.querySelector('.modal')) continue;
          if(!readerInert.has(sibling)){readerInert.set(sibling,sibling.inert);sibling.inert=true;}
        }
        branch=branch.parentElement;
        if(branch===document.body)break;
      }
    } else if(readerOpen) {
      readerOpen=false;restoreReaderBackground();
      const restore=readerOpener;readerOpener=null;
      if(restore?.isConnected && !restore.closest('[inert]'))restore.focus();
    }
  }
  function trapTab(event,root) {
    if(event.key!=='Tab')return;
    const nodes=[...root.querySelectorAll(focusable)].filter(n=>n.getClientRects().length && !n.closest('[inert]'));
    if(!nodes.length){event.preventDefault();root.focus();return;}
    const first=nodes[0],last=nodes.at(-1);
    if(event.shiftKey && (document.activeElement===first || !root.contains(document.activeElement))){event.preventDefault();last.focus();}
    else if(!event.shiftKey && (document.activeElement===last || !root.contains(document.activeElement))){event.preventDefault();first.focus();}
  }
  document.addEventListener('focusin',event=>{lastFocus=event.target;});
  function decorate() {
    decorateReader();
    document.querySelectorAll('input,textarea,select').forEach(input=>{
      if (!input.labels?.length && !input.hasAttribute('aria-label')) input.setAttribute('aria-label', input.title || input.placeholder || ({'filter-category':'按分类筛选','filter-tag':'按标签筛选','filter-sort':'文献排序','reader-page-input':'阅读页码','file-input':'添加文献文件'}[input.id]) || input.id?.replaceAll('-',' ') || '文件选择');
    });
    document.querySelectorAll('button[title]').forEach(button=>{if(!button.hasAttribute('aria-label'))button.setAttribute('aria-label',button.title);});
    document.querySelectorAll('.src-item,.source-card,.chat-source,.reader-outline [data-page],.cite-source,[data-source-index]').forEach(node=>{
      if (!node.matches('button,a') && !node.hasAttribute('tabindex')) {node.tabIndex=0;node.setAttribute('role','button');}
    });
    document.querySelectorAll('.modal').forEach(modal=>{
      modal.setAttribute('role','dialog');modal.setAttribute('aria-modal','true');
      const heading=modal.querySelector('h3,h2');
      if(heading){if(!heading.id)heading.id=modal.id+'-heading';modal.setAttribute('aria-labelledby',heading.id);}
      const visible=!modal.classList.contains('hidden');
      if(visible && !opened.has(modal)){
        opened.set(modal,lastFocus);
        queueMicrotask(()=>{ if(!modal.contains(document.activeElement)) (modal.querySelector('textarea,input:not([type=file]),button')||modal).focus(); });
      } else if(!visible && opened.has(modal)){
        const restore=opened.get(modal);opened.delete(modal);if(restore?.isConnected)restore.focus();
      }
    });
    document.querySelectorAll('.tabs').forEach(tabs=>{
      tabs.setAttribute('role','tablist');tabs.querySelectorAll('[data-tab]').forEach(tab=>{tab.setAttribute('role','tab');tab.setAttribute('aria-selected',String(tab.classList.contains('active')));});
    });
  }
  document.addEventListener('keydown',event=>{
    const modal=[...opened.keys()].at(-1);
    if(modal){
      if(event.key==='Escape'){event.preventDefault();event.stopImmediatePropagation();const close=modal.querySelector('.modal-close');if(close)close.click();else modal.classList.add('hidden');return;}
      trapTab(event,modal);
      return;
    }
    const reader=document.getElementById('reader');
    const dialog = event.target.closest('[aria-modal="true"]');
    if(readerOpen && reader && !event.target.closest('#app-titlebar,#clipboard-prompt') && (!dialog || dialog===reader))trapTab(event,reader);
    const node=event.target;
    if((event.key==='Enter'||event.key===' ') && node.matches('[role="button"][tabindex]') && !node.matches('.paper-row-item,.paper-card')){event.preventDefault();node.click();}
    if(['ArrowRight','ArrowLeft','Home','End'].includes(event.key)){
      const tabs=node.closest('[role=tablist]'), tab=node.closest('[role=tab]');
      if(tabs && tab){
        const items=[...tabs.querySelectorAll('[role=tab]')].filter(n=>!n.disabled && n.getClientRects().length);
        const index=items.indexOf(tab);if(index<0)return;
        const next=items[event.key==='Home'?0:event.key==='End'?items.length-1:(index+(event.key==='ArrowRight'?1:-1)+items.length)%items.length];
        next?.click();next?.focus();event.preventDefault();
      }
    }
  },true);
  new MutationObserver(decorate).observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['class']});
  decorate();
}
