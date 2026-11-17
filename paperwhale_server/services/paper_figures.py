"""从论文 PDF 里抽原图，作为阅读对话的配图。"""
from __future__ import annotations

import hashlib
import io
import threading
import time
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from .. import config, db

_PRUNE_LOCK = threading.Lock()
_LAST_PRUNE = 0.0

MAX_WIDTH = 1400
MAX_IMAGES = 3


def _prune(folder: Path, days: int = 14, max_bytes: int = 512 * 1024 * 1024) -> None:
    global _LAST_PRUNE
    now = time.time()
    with _PRUNE_LOCK:
        if now - _LAST_PRUNE < 600:
            return
        _LAST_PRUNE = now
    cutoff = now - days * 86400
    try:
        files = [(item.stat().st_mtime, item.stat().st_size, item) for item in folder.iterdir() if item.is_file()]
    except OSError:
        return
    files.sort()
    total = sum(size for _, size, _ in files)
    for mtime, size, item in files:
        if mtime < cutoff or total > max_bytes:
            try:
                item.unlink(missing_ok=True)
                total -= size
            except OSError:
                pass


def cache_dir() -> Path:
    folder = config.DATA_DIR / 'paper-figures'
    folder.mkdir(parents=True, exist_ok=True)
    _prune(folder)
    return folder


def _paper(paper_id: str) -> dict | None:
    row = db.query_one('SELECT id, title, file_path FROM papers WHERE id=?', (paper_id,))
    return dict(row) if row else None


def _save_image(image: Image.Image, key: str) -> dict | None:
    if image.width < 240 or image.height < 160:
        return None
    ratio = image.width / max(1, image.height)
    if ratio < 0.35 or ratio > 2.8:  # 过宽的条幅/广告图不算论文配图
        return None
    if image.width > MAX_WIDTH:
        image = image.resize((MAX_WIDTH, max(1, round(image.height * MAX_WIDTH / image.width))), Image.LANCZOS)
    if image.mode not in {'RGB', 'L'}:
        image = image.convert('RGB')
    folder = cache_dir()
    name = hashlib.sha256(key.encode('utf-8')).hexdigest()[:20] + '.jpg'
    target = folder / name
    if not target.exists():
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=86, optimize=True)
        target.write_bytes(buffer.getvalue())
    return {'url': f'/api/paper-figures/{name}', 'width': image.width, 'height': image.height}


def extract(paper_id: str, pages: list[int] | None = None, limit: int = MAX_IMAGES) -> list[dict]:
    """按来源页码抽取论文内嵌图；没有页码时从前几页里找。"""
    paper = _paper(paper_id)
    if not paper or not str(paper.get('file_path') or '').strip():
        return []
    path = config.resolve_pdf_path(str(paper['file_path']))
    if not path.is_file():
        return []
    try:
        document = pdfium.PdfDocument(str(path))
    except Exception:  # noqa: BLE001
        return []
    title = str(paper.get('title') or '论文')
    try:
        total = len(document)
        wanted = [page for page in (pages or []) if 1 <= int(page) <= total][:4]
        if not wanted:
            wanted = list(range(1, min(total, 4) + 1))
        results: list[dict] = []
        for page_number in wanted:
            if len(results) >= limit:
                break
            page = document[page_number - 1]
            objects = sorted(
                (obj for obj in page.get_objects() if str(obj.type) == '3'),
                key=lambda obj: (obj.get_bounds()[2] - obj.get_bounds()[0]) * (obj.get_bounds()[3] - obj.get_bounds()[1]),
                reverse=True,
            )
            for index, obj in enumerate(objects):
                if len(results) >= limit:
                    break
                bitmap = None
                try:
                    bitmap = obj.get_bitmap()
                    image = bitmap.to_pil().convert('RGB')
                except Exception:  # noqa: BLE001
                    continue
                try:
                    saved = _save_image(image, f'{paper_id}:{page_number}:{index}')
                finally:
                    try:
                        image.close()
                    except Exception:  # noqa: BLE001
                        pass
                if not saved:
                    continue
                saved.update({'source_url': '', 'title': f'{title[:60]} · 第 {page_number} 页'})
                results.append(saved)
                break
        return results
    finally:
        try:
            document.close()
        except Exception:  # noqa: BLE001
            pass
