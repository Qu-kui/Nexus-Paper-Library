/* Pure reader helpers shared by PDF search and paper-scoped reading memory. */
export function findTextMatches(items, query) {
  if (!query) return [];
  const segments = [];
  let text = '';
  for (const [itemIndex,item] of items.entries()) {
    if (itemIndex) text += ' ';
    const str = String(item.str || '');
    segments.push({itemIndex,start:text.length,end:text.length+str.length});
    text += str;
  }
  const pattern = new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'giu');
  const matches = [];
  for (const hit of text.matchAll(pattern)) {
    const start = hit.index, end = start + hit[0].length;
    const parts = segments.filter(s=>s.end>start && s.start<end).map(s=>({itemIndex:s.itemIndex,start:Math.max(0,start-s.start),end:Math.min(s.end,end)-s.start}));
    matches.push({start,end,parts});
  }
  return matches;
}
export function saveReadingState(storage, id, value) {
  try { storage.setItem(`pw-reader:${id}`,JSON.stringify(value)); } catch { /* Private/disabled storage must never block reading. */ }
}
export function loadReadingState(storage,id) {
  try { const value=JSON.parse(storage.getItem(`pw-reader:${id}`)); return value && typeof value==='object' ? value : null; } catch { return null; }
}
export function appendNote(existing,answer) { return [String(existing||'').trim(),String(answer||'').trim()].filter(Boolean).join('\n\n'); }

// Retain PDF.js item/UTF-16 offsets while tolerating extraction line breaks,
// hyphenation, punctuation and common Unicode ligatures.
export function findSourceMatch(items, snippet) {
  const normalize = value => String(value || '').normalize('NFKC').toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
  const target = normalize(snippet);
  if (target.length < 12) return null;
  let text = '';
  const positions = [];
  for (const [itemIndex, item] of items.entries()) {
    let offset = 0;
    for (const character of String(item.str || '')) {
      const normalized = normalize(character);
      for (let i = 0; i < normalized.length; i++) positions.push({itemIndex, start:offset, end:offset+character.length});
      text += normalized;
      offset += character.length;
    }
  }
  let needle = target, start = text.indexOf(needle);
  // The tail usually contains prose when the excerpt starts with a running header.
  if (start < 0 && target.length >= 48) {
    for (let offset = target.length - 48; offset >= 0; offset -= 24) {
      needle = target.slice(offset, offset + 48);
      start = text.indexOf(needle);
      if (start >= 0) break;
    }
  }
  if (start < 0) return null;
  const parts = [];
  for (const position of positions.slice(start, start + needle.length)) {
    const previous = parts.at(-1);
    if (previous?.itemIndex === position.itemIndex) previous.end = position.end;
    else parts.push({...position});
  }
  return {parts};
}
