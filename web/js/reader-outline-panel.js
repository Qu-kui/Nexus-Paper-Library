/* Owned, cancellable chapter scanning. Embedded PDF outlines remain preferred. */
import {extractHeadings,MAX_OUTLINE_SCAN_PAGES} from './reader-outline.js';
import {resolvePdfDestination} from './reader-navigation.js';

export function installOutlineNavigator({getState,navigate,onChange}) {
  const box=document.getElementById('reader-outline');
  let serial=0,currentDoc=null,source='',scanning=false;
  const button=(label,action)=>{const node=document.createElement('button');node.type='button';node.className='btn tiny';node.textContent=label;node.addEventListener('click',action);return node;};
  const cacheKey=()=>{const s=getState();return 'pw-reader-chapters:'+s.paperId+':'+(s.doc?.fingerprints?.[0]||'unknown');};
  function persist(){try{localStorage.setItem(cacheKey(),JSON.stringify(getState().chapters));}catch{}}
  function changed(){getState().chapters.sort((a,b)=>a.page-b.page);onChange?.();}
  function editor(index=-1) {
    box.querySelector('.reader-chapter-editor')?.remove();
    const state=getState(),entry=state.chapters[index];
    const form=document.createElement('form');form.className='reader-chapter-editor';
    const title=document.createElement('input');title.type='text';title.maxLength=160;title.required=true;title.value=entry?.title||'';title.placeholder='章节名称';title.setAttribute('aria-label','章节名称');
    const page=document.createElement('input');page.type='number';page.min=1;page.max=state.doc?.numPages||1;page.required=true;page.value=entry?.page||state.currentPage;page.setAttribute('aria-label','章节起始页');
    const save=document.createElement('button');save.type='submit';save.className='btn tiny';save.textContent='保存章节';
    form.append(title,page,save,button('取消',()=>form.remove()));
    if(entry)form.append(button('删除本节',()=>{state.chapters.splice(index,1);source='已手动调整的章节';changed();persist();render();}));
    form.addEventListener('submit',event=>{event.preventDefault();if(!title.value.trim() || !form.reportValidity())return;
      const item={title:title.value.trim(),page:Number(page.value),level:entry?.level||1,offset:Number(page.value)===entry?.page?entry.offset||0:0};
      if(index<0)state.chapters.push(item);else state.chapters[index]=item;
      source='已手动调整的章节';changed();persist();render();
    });box.prepend(form);title.focus();
  }
  function status(text){const node=box.querySelector('.reader-outline-status');if(node)node.textContent=text;}
  function render() {
    if(!box)return;box.replaceChildren();
    const head=document.createElement('div');head.className='reader-outline-actions';
    const note=document.createElement('span');note.className='reader-outline-status muted small';note.setAttribute('role','status');note.textContent=source;
    head.append(note);
    if(scanning)head.append(button('停止识别',()=>{serial++;scanning=false;source='已停止章节识别';render();}));
    else if(currentDoc){head.append(button('重新识别',()=>load(currentDoc,true)),button('添加章节',()=>editor()));}
    box.append(head);
    const chapters=getState().chapters;
    if(!chapters.length&&!scanning){const empty=document.createElement('p');empty.className='muted small';empty.textContent='未识别到可靠章节。可添加章节，或使用顶部页码跳转。';box.append(empty);}
    for(const [index,item] of chapters.entries()) {
      const row=document.createElement('div');row.className='reader-chapter-row';
      const link=button(item.title,()=>navigate(item.page,{offset:item.offset||0}));link.classList.add('outline-item');link.style.paddingLeft=`${8+Math.min(4,item.level-1)*12}px`;link.title=`第 ${item.page} 页`;
      const page=document.createElement('span');page.className='muted small';page.textContent=String(item.page);
      const edit=button('调整',()=>editor(index));edit.setAttribute('aria-label',`调整章节 ${item.title}`);
      row.append(link,page,edit);box.append(row);
    }
  }
  async function load(doc,force=false) {
    const request=++serial,state=getState(),owner=state.openToken;currentDoc=doc;scanning=false;
    const owns=()=>request===serial&&getState().doc===doc&&getState().openToken===owner;
    state.chapters=[];source='正在读取章节导航';render();changed();
    if(force)try{localStorage.removeItem(cacheKey());}catch{}
    if(!force)try {
      const saved=JSON.parse(localStorage.getItem(cacheKey())||'null');
      if(Array.isArray(saved)&&saved.length<=400&&saved.every(row=>typeof row.title==='string'&&row.title.length<=160&&Number.isInteger(row.page)&&row.page>=1&&row.page<=doc.numPages)) {
        state.chapters=saved.map(row=>({title:row.title,page:row.page,level:Math.min(5,Math.max(1,Number(row.level)||1)),offset:Math.max(0,Math.min(1,Number(row.offset)||0))}));source='已手动调整的章节';changed();render();return;
      }
    }catch{}
    let native;
    if(!force)try{native=await doc.getOutline();}catch{}
    if(!owns())return;
    if(Array.isArray(native)&&native.length) {
      const flat=[];
      async function visit(items,level=1) {
        for(const item of items){if(!owns()||flat.length>=400||level>5)return;
          const target=await resolvePdfDestination(doc,item.dest);
          if(!owns())return;
          if(target&&item.title){
            let offset=0;
            if(Number.isFinite(target.y)){
              const page=await doc.getPage(target.page);if(!owns())return;
              const viewport=page.getViewport({scale:1});
              offset=Math.max(0,Math.min(1,viewport.convertToViewportPoint(target.x||0,target.y)[1]/viewport.height));
            }
            flat.push({title:String(item.title).slice(0,160),page:target.page,level,offset});
          }
          if(Array.isArray(item.items))await visit(item.items,level+1);
        }
      }
      await visit(native);if(!owns())return;
      if(flat.length){state.chapters=flat;source='PDF 内置目录';changed();render();return;}
    }
    scanning=true;source='自动识别章节（本机处理）';render();
    const pages=[],count=Math.min(doc.numPages,MAX_OUTLINE_SCAN_PAGES);
    try {
      for(let number=1;number<=count;number++) {
        if(!owns())return;
        status(`自动识别章节 ${number} / ${count} 页`);
        const page=await doc.getPage(number);if(!owns())return;
        const content=await page.getTextContent();if(!owns())return;
        pages.push({page:number,items:content.items||[]});
        await new Promise(resolve=>setTimeout(resolve,0));
      }
      if(!owns())return;
      state.chapters=extractHeadings(pages);source=`自动识别章节 · 可调整${count<doc.numPages?` · 仅前 ${count} 页`:''}`;
      changed();
    }catch{if(!owns())return;source='章节识别未完成，可以重新识别';}
    if(owns()){scanning=false;render();}
  }
  return {load,reset:()=>{serial++;currentDoc=null;scanning=false;source='';getState().chapters=[];box?.replaceChildren();onChange?.();}};
}
