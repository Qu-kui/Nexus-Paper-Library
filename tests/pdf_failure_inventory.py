"""Read-only PDF failure summary. Does not read settings or fetch any remote URL."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

sys.stdout.reconfigure(encoding='utf-8',errors='replace')

path = Path(os.environ.get('APPDATA',''))/'PaperWhale'/'paperwhale.db'
if not path.is_file():
    print(json.dumps({'available':False}))
else:
    conn = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)
    try:
        conn.execute('PRAGMA query_only=ON')
        rows = conn.execute("SELECT doi,url,pdf_error FROM papers WHERE COALESCE(file_path,'')='' ").fetchall()
        print(json.dumps({'available':True, 'missing':len(rows),
            'doi_prefixes':dict(Counter((doi or '').split('/')[0] or 'none' for doi,_,_ in rows)),
            'landing_hosts':dict(Counter(urlparse(url or '').hostname or 'none' for _,url,_ in rows)),
            'errors':dict(Counter((error or '未记录')[:160] for _,_,error in rows))}, ensure_ascii=False))
    finally:
        conn.close()
