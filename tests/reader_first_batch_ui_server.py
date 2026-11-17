"""Only synthetic PDFs and fresh temporary data, never an installed user library."""
from __future__ import annotations
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
if not os.environ.get('PAPERWHALE_UI_TEST') or not os.environ.get('PAPERWHALE_DATA'):
    raise SystemExit('Use reader_first_batch_ui.mjs')
os.environ['PAPERWHALE_LLM_MOCK']='1'
from paperwhale_server import config,db
from paperwhale_server.services import metadata,websearch


def synthetic_pdf(native=False):
    objects=[b'<< /Type /Catalog /Pages 2 0 R'+(b' /Outlines 13 0 R' if native else b'')+b' >>',
             b'<< /Type /Pages /Kids [4 0 R 6 0 R 8 0 R] /Count 3 >>',
             b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    for index,title in enumerate(('1 Introduction','2 Methods','3 Results')):
        stream=f'BT /F1 17 Tf 60 740 Td ({title}) Tj /F1 11 Tf 0 -35 Td (This is synthetic evidence on page {index+1}.) Tj 0 -50 Td (Jump reference to results) Tj ET'.encode()
        objects.extend([f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 3 0 R >> >> /Contents {5+index*2} 0 R'.encode()+
                        (b' /Annots [10 0 R 11 0 R 12 0 R]' if index==0 else b'')+b' >>',
                        f'<< /Length {len(stream)} >>\nstream\n'.encode()+stream+b'\nendstream'])
    objects.extend([b'<< /Type /Annot /Subtype /Link /Rect [60 640 225 665] /Border [0 0 0] /Dest [8 0 R /XYZ 0 700 null] >>',
                    b'<< /Type /Annot /Subtype /Link /Rect [250 640 350 665] /Border [0 0 0] /A << /S /URI /URI (https://example.com/paper) >> >>',
                    b'<< /Type /Annot /Subtype /Link /Rect [400 640 500 665] /Border [0 0 0] /A << /S /URI /URI (javascript:alert\(1\)) >> >>'])
    if native: objects.extend([b'<< /Type /Outlines /First 14 0 R /Last 14 0 R /Count 1 >>',
                              b'<< /Title (Native section) /Parent 13 0 R /Dest [6 0 R /XYZ 0 650 null] >>'])
    result=bytearray(b'%PDF-1.7\n');offsets=[0]
    for number,body in enumerate(objects,1):
        offsets.append(len(result));result.extend(f'{number} 0 obj\n'.encode()+body+b'\nendobj\n')
    xref=len(result)
    result.extend(f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]:result.extend(f'{offset:010} 00000 n \n'.encode())
    result.extend(f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF'.encode())
    return bytes(result)


config.update_settings({'scholar_auto_sync':False,'pdf_browser_session_enabled':False})
db.init_db()
for pid in ('a','b','native'):
    path=config.DATA_DIR/(pid+'.pdf');path.write_bytes(synthetic_pdf(pid=='native'))
    db.execute('INSERT INTO papers(id,title,authors,doi,file_path,status) VALUES(?,?,?,?,?,?)',
               (pid,'PAPER '+pid.upper(),'Synthetic Author','10.0000/'+pid,str(path),'indexed'))
    for page in range(1,4):db.execute('INSERT INTO chunks(paper_id,page,text) VALUES(?,?,?)',(pid,page,f'Synthetic page {page} evidence of {pid}'))
for pid,page,color,quote,note in [('a',1,'#ffd666','FIRST SOURCE QUOTE','FIRST NOTE'),('a',2,'#8ee6a8','SECOND SOURCE QUOTE',''),('a',3,'#ffd666','THIRD SOURCE QUOTE','THIRD NOTE'),('b',1,'#ff9f9f','B ONLY QUOTE','B NOTE')]:
    db.execute('INSERT INTO annotations(paper_id,page,color,quote,note,rects,kind) VALUES(?,?,?,?,?,?,?)',
               (pid,page,color,quote,note,'[{"x":0.1,"y":0.3,"w":0.2,"h":0.02}]','highlight'))
metadata.repair_bad_titles=lambda:None
websearch.search=lambda *a,**kw:[]
from paperwhale_server.main import app
import uvicorn
uvicorn.run(app,host='127.0.0.1',port=int(sys.argv[1]),log_level='warning')
