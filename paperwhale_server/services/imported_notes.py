"""Loss-averse note import: separate provenance, content deduplication, no personal-note overwrite."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from contextlib import closing
from html.parser import HTMLParser

from .. import db


class _NoteHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.suppressed = 0
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag in {'script', 'style', 'iframe', 'object'}:
            self.suppressed += 1
        if self.suppressed:
            return
        attributes = dict(attrs)
        if tag in {'p', 'div', 'blockquote', 'table', 'tr', 'ul', 'ol', 'pre'}:
            self.parts.append('\n\n')
        elif tag == 'br':
            self.parts.append('\n')
        elif tag == 'li':
            self.parts.append('\n- ')
        elif tag in {'h1', 'h2', 'h3', 'h4'}:
            self.parts.append('\n\n' + '#' * int(tag[1]) + ' ')
        elif tag in {'b', 'strong'}:
            self.parts.append('**')
        elif tag in {'i', 'em'}:
            self.parts.append('*')
        elif tag in {'td', 'th'}:
            self.parts.append(' | ')
        elif tag == 'img':
            self.parts.append('[图片未迁移' + (': ' + attributes['alt'] if attributes.get('alt') else '') + ']')
        elif tag == 'a':
            url = attributes.get('href') or ''
            safe = bool(re.match(r'^https?://', url, re.I)) and not re.search(r'[\s<>"\x00-\x1f]', url)
            self.links.append(url.replace(')', '%29') if safe else '')
            if safe:
                self.parts.append('[')

    def handle_endtag(self, tag):
        if tag in {'script', 'style', 'iframe', 'object'}:
            self.suppressed = max(0, self.suppressed - 1)
            return
        if self.suppressed:
            return
        if tag in {'p', 'div', 'blockquote', 'table', 'tr', 'ul', 'ol', 'pre', 'h1', 'h2', 'h3', 'h4'}:
            self.parts.append('\n\n')
        elif tag in {'b', 'strong'}:
            self.parts.append('**')
        elif tag in {'i', 'em'}:
            self.parts.append('*')
        elif tag == 'a' and self.links:
            url = self.links.pop()
            if url:
                self.parts.append('](' + url + ')')

    def handle_data(self, data):
        if not self.suppressed:
            self.parts.append(data)


def text(raw: str) -> str:
    value = str(raw or '').replace('\r\n', '\n').replace('\r', '\n')
    if re.search(r'</?(?:p|div|br|h[1-6]|b|strong|i|em|ul|ol|li|a|img|table|script|style|span)\b', value, re.I):
        parser = _NoteHTML()
        parser.feed(value); parser.close()
        value = ''.join(parser.parts)
    value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', value)
    value = '\n'.join(line.rstrip() for line in value.splitlines())
    return re.sub(r'\n{3,}', '\n\n', value).strip()


def note(raw: str, label: str = '迁移笔记') -> dict | None:
    content = text(raw)
    return {'label': label, 'content': content} if content else None


def _decode(raw):
    entries = json.loads(raw or '[]')
    if not isinstance(entries, list) or any(not isinstance(n, dict) or not isinstance(n.get('content'), str) for n in entries):
        raise ValueError('原迁移笔记格式异常，已保留原数据，请先备份后检查')
    return entries


def read(paper_id: str) -> dict | None:
    row = db.query_one('SELECT notes,imported_notes FROM papers WHERE id=?', (paper_id,))
    if row is None:
        return None
    return {'personal': row['notes'] or '', 'imported': _decode(row['imported_notes'])}


def merge(paper_id: str, notes: list[dict], *, source: str) -> tuple[int, int]:
    if not notes:
        return 0, 0
    with closing(db.connect()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT imported_notes FROM papers WHERE id=?', (paper_id,)).fetchone()
        if row is None:
            raise ValueError('论文不存在，未导入笔记')
        entries = _decode(row['imported_notes'])
        def fingerprint(content):
            canonical = unicodedata.normalize('NFC', '\n'.join(line.rstrip() for line in content.strip().splitlines()))
            return hashlib.sha256(canonical.encode('utf-8')).hexdigest()
        seen = {fingerprint(entry['content']) for entry in entries}
        added = duplicate = 0
        for entry in notes:
            content = str(entry.get('content') or '').strip()
            if not content:
                continue
            if len(content) > 1_000_000:
                raise ValueError('单条笔记超过100万字符，未截断；请拆分笔记后重新导入')
            identity = fingerprint(content)
            if identity in seen:
                duplicate += 1
                continue
            entries.append({'id': identity, 'source': source, 'label': str(entry.get('label') or '迁移笔记'),
                            'content': content, 'imported_at': db.now()})
            seen.add(identity); added += 1
        if added:
            conn.execute('UPDATE papers SET imported_notes=?,updated_at=? WHERE id=?',
                         (json.dumps(entries, ensure_ascii=False), db.now(), paper_id))
    return added, duplicate
