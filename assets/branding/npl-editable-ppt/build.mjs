import {createRequire} from 'node:module';
import {writeFileSync} from 'node:fs';
import {dirname,join} from 'node:path';
import {fileURLToPath} from 'node:url';
const require=createRequire('C:/Users/10498/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/package.json');
const PptxGenJS=require('pptxgenjs');
const out=dirname(fileURLToPath(import.meta.url));
const pptx=new PptxGenJS();
pptx.defineLayout({name:'NPL_BOARD',width:12,height:8});pptx.layout='NPL_BOARD';
pptx.author='NPL';pptx.subject='Editable NPL logo';pptx.title='Nexus Paper Library — editable logo';
pptx.company='Nonlinear Photonics Lab';pptx.lang='zh-CN';
pptx.theme={headFontFace:'Arial',bodyFontFace:'Microsoft YaHei',lang:'zh-CN'};
const P='78006E',M='947799',BG='FFFEFC';
const unit=12/1536, slides=[];
const C=(...v)=>v;
// Native book curves and positive-space letter strokes, in the source board's coordinates.
const letter=(points)=>points.map((p,i)=>[i?'L':'M',197+p[0],146+p[0]*126/235+p[1]]).concat([['Z']]);
const strokes=[
 ['左书页_弧线',[C('M',197,101),C('C',300,101,414,162,443,256),C('C',420,216,310,158,197,135),C('Z')]],
 ['右书页_弧线',[C('M',443,256),C('C',466,163,572,100,701,100),C('L',701,135),C('C',587,172,488,212,443,256),C('Z')]],
 ['N_左竖笔',letter([[0,0],[84,0],[84,265],[0,265]])],
 ['N_斜笔',letter([[0,0],[84,0],[235,265],[151,265]])],
 ['N_右竖笔',letter([[151,0],[235,0],[235,265],[151,265]])],
 ['L_书脊与底边',[C('M',454,279),C('L',476,267),C('L',476,487),C('L',701,375),C('L',701,428),C('L',454,542),C('Z')]],
 ['P_右侧笔画',[C('M',454,279),C('Q',454,266,468,258),C('L',701,147),C('L',701,344),C('L',547,422),C('L',547,350),C('Q',547,339,560,332),C('L',634,297),C('Q',645,291,645,280),C('L',645,239),C('Q',645,224,631,231),C('L',531,278),C('Q',517,285,517,300),C('L',517,470),C('L',454,503),C('Z')]],
];
function points(commands){return commands.flatMap(c=>{let a=[];for(let i=1;i<c.length;i+=2)a.push([c[i],c[i+1]]);return a;});}
function bounds(commands){const a=points(commands);const x=Math.min(...a.map(v=>v[0])),y=Math.min(...a.map(v=>v[1]));return{x,y,w:Math.max(...a.map(v=>v[0]))-x,h:Math.max(...a.map(v=>v[1]))-y};}
function page(){const slide=pptx.addSlide();slide.background={color:BG};const meta={parts:[],groups:[]};slides.push(meta);return{slide,meta};}
function text(ctx,t,x,y,w,h,size,color=P,bold=false,extra={}){ctx.slide.addText(t,{objectName:t.slice(0,35),x:x*unit,y:y*unit,w:w*unit,h:h*unit,fontFace:/[\u4e00-\u9fff]/.test(t)?'Microsoft YaHei':'Arial',fontSize:size,color,bold,margin:0,breakLine:false,valign:'mid',...extra});}
function line(ctx,x1,y1,x2,y2,color='B59AB7'){ctx.slide.addShape(pptx.ShapeType.line,{x:x1*unit,y:y1*unit,w:(x2-x1)*unit,h:(y2-y1)*unit,line:{color,width:.6}});}
function logo(ctx,id,x,y,width,color=P){
 const s=width/504,tx=x-197*s,ty=y-100*s,names=[];
 for(const[label,commands]of strokes){
  const transformed=commands.map(c=>[c[0],...c.slice(1).map((v,i)=>v*s+(i%2?ty:tx))]);
  const b=bounds(transformed),name=id+'__'+label;names.push(name);
  ctx.slide.addShape(pptx.ShapeType.rect,{objectName:name,x:b.x*unit,y:b.y*unit,w:b.w*unit,h:b.h*unit,fill:{color},line:{color,transparency:100}});
  ctx.meta.parts.push({name,commands:transformed,bounds:b});
 }
 ctx.meta.groups.push({name:id,members:names});return id;
}
function app(ctx,id,x,y,size){
 const bg=id+'__紫色背景';ctx.slide.addShape(pptx.ShapeType.roundRect,{objectName:bg,x:x*unit,y:y*unit,w:size*unit,h:size*unit,radius:size*.2*unit,rectRadius:size*.2*unit,fill:{color:P},line:{color:P,transparency:100},radius:20});
 const mark=logo(ctx,id+'__反白标志',x+size*.12,y+size*.16,size*.76,'FFFFFF');
 ctx.meta.groups.push({name:id,members:[bg,mark]});
}

// Page 1: the complete reference composition, rebuilt entirely as native objects.
{
 const ctx=page();logo(ctx,'主标志',197,100,504);
 text(ctx,'NPL',752,147,670,260,150,P,false,{fontFace:'Arial Black'});
 text(ctx,'Nexus Paper Library',752,391,710,88,38,P,true);
 text(ctx,'论文 · 阅读 · 知识',758,475,650,70,25,M,false,{charSpacing:4});
 line(ctx,64,606,1472,606);app(ctx,'应用图标',448,643,256);line(ctx,779,660,779,879);logo(ctx,'单色标志',862,670,225);
 text(ctx,'Nonlinear Photonics Lab',556,931,520,48,14,M,false,{charSpacing:2,align:'center'});line(ctx,458,953,560,953);line(ctx,1080,953,1178,953);
 ctx.slide.addNotes('图形为原生自由形状。标志可右键取消组合后，分别调整书页、N的三根笔画、P和L。字样为可编辑文本。');
}
// Page 2: large master for precise edits.
{
 const ctx=page();text(ctx,'Logo 可编辑母版',86,48,980,72,31,P,true);
 logo(ctx,'可编辑母版',230,208,720);
 text(ctx,'可单独选择的部件',1080,250,380,54,21,P,true);
 text(ctx,'左书页 / 右书页\nN 左竖笔 / 斜笔 / 右竖笔\nP 右侧笔画\nL 书脊与底边',1080,325,380,235,18,'6D5C73',false,{breakLine:false,paraSpaceAfterPt:14});
 text(ctx,'选中标志后右键取消组合；右键“编辑顶点”可调整曲线与镂空。',86,938,1380,50,16,'6D5C73');
 ctx.slide.addNotes('选择窗格中的部件名对应顶部两片书页、N的两竖一斜、P以及L。调整笔画前可复制一份母版。');
}
// Page 3: editable background and reverse mark.
{
 const ctx=page();text(ctx,'图标与配色',86,48,980,72,31,P,true);
 app(ctx,'反白应用图标',225,241,410);logo(ctx,'紫色独立标志',900,264,420);
 text(ctx,'紫底反白',225,720,410,58,24,P,true,{align:'center'});text(ctx,'紫色单色',900,720,420,58,24,P,true,{align:'center'});
 text(ctx,'背景和标志可分别改色。按住 Shift 等比例缩放；复制到其他 PPT 后仍可编辑。',86,938,1380,50,16,'6D5C73');
 ctx.slide.addNotes('应用图标包含紫色背景与内部标志分组；可取消组合后单独编辑背景圆角、背景色和反白笔画。');
}
writeFileSync(join(out,'geometry.json'),JSON.stringify({unit,slides},null,2));
await pptx.writeFile({fileName:join(out,'base.pptx')});
console.log('Wrote native PowerPoint layout and geometry metadata');
