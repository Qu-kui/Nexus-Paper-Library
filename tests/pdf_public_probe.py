"""Optional real-network control: download one public arXiv PDF into a temporary directory."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from paperwhale_server import config
from paperwhale_server.services import pdf_download, pdf_trace

with tempfile.TemporaryDirectory(prefix='pw-public-pdf-') as folder:
    root=Path(folder)
    with patch.multiple(config,DATA_DIR=root,DEFAULT_PDF_DIR=root/'pdfs',TMP_DIR=root/'tmp',
                        DB_PATH=root/'test.db',SETTINGS_PATH=root/'settings.json',_cache=dict(config.DEFAULT_SETTINGS)):
        trace=pdf_trace.Trace()
        token=pdf_trace.active.set(trace)
        try:
            result=pdf_download.download('https://arxiv.org/pdf/1706.03762',route='public-control')
            identity=pdf_download.verify_identity(result.path,{'title':'Attention Is All You Need'})
            print(json.dumps({'ok':True,'bytes':result.path.stat().st_size,'identity':identity,'attempts':trace.attempts},ensure_ascii=True))
        except pdf_download.PdfDownloadError as exc:
            print(json.dumps({'ok':False,'code':exc.code,'attempts':trace.attempts},ensure_ascii=True))
            raise SystemExit(1)
        finally:
            pdf_trace.active.reset(token)
