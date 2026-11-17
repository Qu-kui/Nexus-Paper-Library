"""Conservative, offline separation of journal names from copied citation headers."""
import html
import re

DOI=re.compile(r'10\.\d{4,9}/[-._;()/:<>a-zA-Z0-9]+',re.I)
CITATION=re.compile(r'\b(?P<volume>\d{1,4})\s*[（(]\s*(?P<issue>\d{1,4})\s*[）)]\s*[:：]\s*(?P<pages>[A-Za-z]?\d+(?:\s*[-–—]\s*[A-Za-z]?\d+)?)')


def clean_venue_fields(fields):
    """Only split explicit citation syntax; never guess a missing journal from a DOI."""
    result=dict(fields)
    if 'venue' not in result:return result
    value=result.get('venue') or ''
    if isinstance(value,(list,tuple)):value='; '.join(str(v) for v in value if v)
    raw=html.unescape(str(value))
    raw=re.sub(r'<[^>]*>',' ',raw)
    raw=re.sub(r'\s+',' ',raw.replace('\ufeff','')).strip()
    inferred={}
    doi=DOI.search(raw)
    if doi:inferred['doi']=doi.group().rstrip('.,;)]}>"\'')
    citation=CITATION.search(raw)
    if citation:
        inferred.update({k:v.strip().replace('—','–') for k,v in citation.groupdict().items()})
    vol=re.search(r'\bVol(?:ume)?\.?\s*[:：]?\s*(\d{1,4})\b',raw,re.I)
    issue=re.search(r'\b(?:No\.?|Issue)\s*[:：]?\s*(\d{1,4})\b',raw,re.I)
    if vol:inferred.setdefault('volume',vol[1])
    if issue:inferred.setdefault('issue',issue[1])
    text=re.split(r'(?:\bDOI\b\s*[:：]?|https?://(?:dx\.)?doi\.org/|10\.\d{4,9}/)',raw,maxsplit=1,flags=re.I)[0]
    # Trailing volume/issue/year/DOI runs are citation data, not part of the name.
    tail=CITATION.search(text)
    if tail:text=text[:tail.start()]
    text=re.split(r'\bVol(?:ume)?\.?\s*\d|\bIssue\s*\d',text,maxsplit=1,flags=re.I)[0]
    if citation or vol or issue:
        text=re.sub(r'[,，;；]?\s*[（(]?(?:19|20)\d{2}[）)]?\s*[,，;；]?\s*$','',text)
    text=text.strip(' ,，;；:。')
    # Chinese PDF headers often place the volume before the journal name.
    leading=re.match(r'^(?:第\s*)?(\d{1,4})(?:\s*卷\s*|\s+)([\u4e00-\u9fff].*)$',text)
    if leading and (re.search(r'学\s*报|期\s*刊|杂\s*志|物\s*理|光\s*学',leading[2])):
        inferred.setdefault('volume',leading[1]);text=leading[2]
    text=re.sub(r'(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])','',text).strip(' ,，;；:。')
    if re.fullmatch(r'[\d\s(),:;./–—-]*',text):text=''
    result['venue']=text
    for key,value in inferred.items():
        if not str(result.get(key) or '').strip():result[key]=value
    return result


def repair_venues(conn):
    """Upgrade only clearly polluted fields; preserve IDs, reading data and ordering."""
    for row in conn.execute("SELECT id,venue,volume,issue,pages,doi FROM papers WHERE COALESCE(venue,'')<>''").fetchall():
        original=dict(row);clean=clean_venue_fields(original)
        changed={key:clean[key] for key in ('venue','volume','issue','pages','doi') if clean.get(key)!=original.get(key)}
        if changed:
            conn.execute('UPDATE papers SET '+','.join(key+'=?' for key in changed)+' WHERE id=?',(*changed.values(),row['id']))
