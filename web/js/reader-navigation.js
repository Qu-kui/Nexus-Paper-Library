/* PDF-native links and per-document reading-position history. */
const point = value => ({page:Math.max(1,Math.floor(Number(value?.page)||1)),offset:Math.min(1,Math.max(0,Number(value?.offset)||0))});
const same = (a,b) => a.page===b.page && Math.abs(a.offset-b.offset)<.003;

export class NavigationHistory {
  constructor(limit=60) {this.limit=limit;this.reset();}
  reset() {this.past=[];this.future=[];}
  get canBack() {return this.past.length>0;}
  get canForward() {return this.future.length>0;}
  record(origin,target) {
    const from=point(origin),to=point(target);
    if(same(from,to))return false;
    if(!this.past.length || !same(this.past.at(-1),from))this.past.push(from);
    this.past=this.past.slice(-this.limit);this.future=[];return true;
  }
  back(current) {if(!this.canBack)return null;this.future.push(point(current));return this.past.pop();}
  forward(current) {if(!this.canForward)return null;this.past.push(point(current));return this.future.pop();}
}

export function safePdfLink(value) {
  if(typeof value!=='string' || /[\x00-\x20\x7f]/.test(value))return '';
  try {const url=new URL(value);return ['https:','http:','mailto:'].includes(url.protocol)?url.href:'';}catch{return '';}
}

export async function resolvePdfDestination(doc,destination) {
  try {
    const dest=typeof destination==='string'?await doc.getDestination(destination):destination;
    if(!Array.isArray(dest) || dest.length<2)return null;
    const index=Number.isInteger(dest[0])?dest[0]:await doc.getPageIndex(dest[0]);
    if(!Number.isInteger(index) || index<0 || index>=doc.numPages)return null;
    const target={page:index+1};
    const mode=dest[1]?.name;
    if(mode==='XYZ') {
      if(Number.isFinite(dest[2]))target.x=dest[2];
      if(Number.isFinite(dest[3]))target.y=dest[3];
    } else if(['FitH','FitBH'].includes(mode) && Number.isFinite(dest[2]))target.y=dest[2];
    return target;
  }catch{return null;}
}

export function installReaderNavigation({position,restore}) {
  const history=new NavigationHistory();
  const tools=document.querySelector('#reader #rt-nav') || document.querySelector('#reader .reader-tools');
  const button=(id,label,step)=>{
    const node=document.createElement('button');node.id=id;node.type='button';node.className='btn tiny';node.textContent=label;
    node.setAttribute('aria-label',label+'到阅读跳转位置');
    node.addEventListener('click',()=>{const target=history[step](position());if(target)restore(target);refresh();});
    tools?.insertBefore(node,tools.firstChild);return node;
  };
  const next=button('reader-history-forward','前进','forward');
  const previous=button('reader-history-back','返回','back');
  function refresh(){previous.disabled=!history.canBack;next.disabled=!history.canForward;}
  refresh();
  return {record:(from,to)=>{history.record(from,to);refresh();},reset:()=>{history.reset();refresh();}};
}

export async function renderPdfLinks({pdfPage,viewport,container,doc,isCurrent,navigate,onError}) {
  if(typeof pdfPage.getAnnotations!=='function')return;
  let annotations;
  try {annotations=await pdfPage.getAnnotations({intent:'display'});}catch{return;}
  if(!isCurrent())return;
  const layer=document.createElement('div');layer.className='reader-pdf-links';
  for(const annotation of annotations) {
    if(annotation.subtype!=='Link' || !Array.isArray(annotation.rect))continue;
    const rectangle=viewport.convertToViewportRectangle(annotation.rect);
    if(!rectangle.every(Number.isFinite))continue;
    const x=Math.max(0,Math.min(rectangle[0],rectangle[2])),y=Math.max(0,Math.min(rectangle[1],rectangle[3]));
    const width=Math.min(viewport.width,Math.max(rectangle[0],rectangle[2]))-x;
    const height=Math.min(viewport.height,Math.max(rectangle[1],rectangle[3]))-y;
    if(width<=0 || height<=0)continue;
    const url=safePdfLink(annotation.url || annotation.unsafeUrl);
    let link;
    if(url) {
      link=document.createElement('a');link.href=url;link.target='_blank';link.rel='noopener noreferrer';
      link.title='打开 PDF 中的链接：'+url;link.setAttribute('aria-label',link.title);
    } else if(annotation.dest || ['NextPage','PrevPage','FirstPage','LastPage'].includes(annotation.action)) {
      link=document.createElement('button');link.type='button';link.title='跳转到 PDF 引用位置';link.setAttribute('aria-label',link.title);
      link.addEventListener('click',async()=>{
        try {
          let target;
          if(annotation.dest)target=await resolvePdfDestination(doc,annotation.dest);
          else target={page:annotation.action==='FirstPage'?1:annotation.action==='LastPage'?doc.numPages:pdfPage.pageNumber+(annotation.action==='NextPage'?1:-1)};
          if(!isCurrent())return;
          if(!target)throw new Error('PDF 引用没有可用的目标页');
          let offset=0;
          if(Number.isFinite(target.y)) {
            const page=await doc.getPage(target.page);
            if(!isCurrent())return;
            const view=page.getViewport({scale:1});
            offset=Math.min(1,Math.max(0,view.convertToViewportPoint(target.x||0,target.y)[1]/view.height));
          }
          if(isCurrent())navigate(target.page,{offset,smooth:false});
        }catch(error){if(isCurrent())onError?.(error);}
      });
    } else continue;
    link.className='reader-pdf-link';
    Object.assign(link.style,{left:x+'px',top:y+'px',width:width+'px',height:height+'px'});
    layer.append(link);
  }
  if(isCurrent()){container.querySelector('.reader-pdf-links')?.remove();container.append(layer);}
}
