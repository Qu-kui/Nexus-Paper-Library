"""Replace placeholders with editable DrawingML paths and native groups."""
from pathlib import Path
import json
import zipfile
from lxml import etree as ET

HERE = Path(__file__).resolve().parent
NS = {'p':'http://schemas.openxmlformats.org/presentationml/2006/main',
      'a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
META = json.loads((HERE/'geometry.json').read_text(encoding='utf-8'))
UNIT = META['unit'] * 914400


def tag(prefix, name): return '{'+NS[prefix]+'}'+name
def el(prefix, local_name, **attrs): return ET.Element(tag(prefix,local_name),{k:str(v) for k,v in attrs.items()})
def pt(parent, name, x, y):
    node=el('a',name);node.append(el('a','pt',x=round(x),y=round(y)));parent.append(node)


def geometry(part):
    b=part['bounds'];factor=1000
    def point(x,y): return (round((x-b['x'])*factor),round((y-b['y'])*factor))
    g=el('a','custGeom')
    for name in ['avLst','gdLst','ahLst','cxnLst']:g.append(el('a',name))
    g.append(el('a','rect',l='0',t='0',r='r',b='b'))
    paths=el('a','pathLst');p=el('a','path',w=round(b['w']*factor),h=round(b['h']*factor),fill='norm',stroke='false',extrusionOk='false')
    current=(0,0)
    for c in part['commands']:
        op=c[0]
        if op=='M':
            current=(c[1],c[2]);pt(p,'moveTo',*point(*current))
        elif op=='L':
            current=(c[1],c[2]);pt(p,'lnTo',*point(*current))
        elif op=='C':
            node=el('a','cubicBezTo')
            for i in [1,3,5]:node.append(el('a','pt',x=point(c[i],c[i+1])[0],y=point(c[i],c[i+1])[1]))
            p.append(node);current=(c[5],c[6])
        elif op=='Q':
            control=(c[1],c[2]);end=(c[3],c[4])
            c1=tuple(current[i]+(control[i]-current[i])*2/3 for i in (0,1))
            c2=tuple(end[i]+(control[i]-end[i])*2/3 for i in (0,1))
            node=el('a','cubicBezTo')
            for xy in [c1,c2,end]:node.append(el('a','pt',x=point(*xy)[0],y=point(*xy)[1]))
            p.append(node);current=end
        elif op=='Z':p.append(el('a','close'))
        else:raise ValueError(op)
    paths.append(p);g.append(paths);return g


def name(node):
    found=node.find('.//p:cNvPr',NS)
    return found.get('name') if found is not None else None


def extent(node):
    xform=node.find('p:spPr/a:xfrm',NS)
    if xform is None:xform=node.find('p:grpSpPr/a:xfrm',NS)
    off=xform.find('a:off',NS);ext=xform.find('a:ext',NS)
    return int(off.get('x')),int(off.get('y')),int(ext.get('cx')),int(ext.get('cy'))


def transform(xml,meta):
    root=ET.fromstring(xml);tree=root.find('p:cSld/p:spTree',NS)
    shapes={name(n):n for n in tree if n.tag==tag('p','sp')}
    for part in meta['parts']:
        shape=shapes[part['name']];props=shape.find('p:spPr',NS)
        old=props.find('a:prstGeom',NS);props.replace(old,geometry(part))
    next_id=max(int(n.get('id')) for n in root.findall('.//p:cNvPr',NS))+1
    for group in meta['groups']:
        children=[next(n for n in tree if name(n)==member) for member in group['members']]
        boxes=[extent(n) for n in children];x=min(v[0] for v in boxes);y=min(v[1] for v in boxes)
        w=max(v[0]+v[2] for v in boxes)-x;h=max(v[1]+v[3] for v in boxes)-y
        wrapper=el('p','grpSp');nv=el('p','nvGrpSpPr');nv.append(el('p','cNvPr',id=next_id,name=group['name']));next_id+=1
        nv.append(el('p','cNvGrpSpPr'));nv.append(el('p','nvPr'));wrapper.append(nv)
        props=el('p','grpSpPr');xf=el('a','xfrm')
        xf.append(el('a','off',x=x,y=y));xf.append(el('a','ext',cx=w,cy=h))
        xf.append(el('a','chOff',x=x,y=y));xf.append(el('a','chExt',cx=w,cy=h));props.append(xf);wrapper.append(props)
        index=min(tree.index(n) for n in children);tree.insert(index,wrapper)
        for n in children:wrapper.append(n)
    assert not root.findall('.//p:pic',NS)
    return ET.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)


target=HERE/'NPL_Logo_可编辑.pptx'
with zipfile.ZipFile(HERE/'base.pptx') as source,zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as output:
    for item in source.infolist():
        data=source.read(item.filename)
        for i,meta in enumerate(META['slides'],1):
            if item.filename==f'ppt/slides/slide{i}.xml':data=transform(data,meta)
        output.writestr(item,data)
with zipfile.ZipFile(target) as archive:
    assert archive.testzip() is None
    for i in range(1,4):
        r=ET.fromstring(archive.read(f'ppt/slides/slide{i}.xml'))
        print(i,'editable paths:',len(r.findall('.//a:custGeom',NS)),'groups:',len(r.findall('.//p:grpSp',NS)),'raster images:',len(r.findall('.//p:pic',NS)))
print(target)
