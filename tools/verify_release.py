"""Offline integrity checks: never executes an installer or accesses user data."""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import struct
import zipfile
from pathlib import Path
from PyInstaller.archive.readers import CArchiveReader

ROOT=Path(__file__).resolve().parents[1]


def digest(data):return hashlib.sha256(data).hexdigest()


def verify_archive(archive,portable,version):
    assert archive.testzip() is None,'ZIP CRC failed'
    names=set(archive.namelist())
    assert ('portable.flag' in names)==portable,'Incorrect portable data mode'
    assert 'PaperWhale.exe' in names,'Application missing'
    exe=archive.read('PaperWhale.exe')
    offset=struct.unpack_from('<I',exe,0x3c)[0]
    assert exe[:2]==b'MZ' and exe[offset:offset+4]==b'PE\0\0' and struct.unpack_from('<H',exe,offset+4)[0]==0x8664,'Not a Windows x64 executable'
    forbidden={'settings.json','paperwhale.db','.env','cookies','cookies.sqlite'}
    assert not any(Path(name).name.lower() in forbidden or name.startswith(('data/','tests/','.venv/')) for name in names),'Unexpected private/runtime data'
    expected_docs=json.loads((ROOT/'tools'/'release-docs.json').read_text(encoding='utf-8'))
    assert {name for name in names if name.startswith('docs/') and not name.endswith('/')}=={'docs/'+name for name in expected_docs},'Unexpected or missing release documentation'
    for name in expected_docs:assert archive.read('docs/'+name)==(ROOT/'docs'/name).read_bytes(),'Stale documentation: '+name
    prefix='_internal/web/' if '_internal/web/index.html' in names else 'web/'
    count=0
    for source in (ROOT/'web').rglob('*'):
        if not source.is_file():continue
        name=prefix+source.relative_to(ROOT/'web').as_posix()
        assert name in names and digest(archive.read(name))==digest(source.read_bytes()),'Missing/stale web resource: '+name
        count+=1
    skill_prefix=prefix.removesuffix('web/')+'skills/'
    for source in (ROOT/'skills').rglob('*'):
        if not source.is_file():continue
        name=skill_prefix+source.relative_to(ROOT/'skills').as_posix()
        assert name in names and archive.read(name)==source.read_bytes(),'Missing/stale bundled skill: '+name
    manifest=json.loads(archive.read('browser-extension/manifest.json'))
    assert manifest['version']==version,'Bundled connector version mismatch'
    word_template='word/PaperWhaleMacros.dotm'
    assert word_template in names and archive.read(word_template)==(ROOT/'word'/'PaperWhaleMacros.dotm').read_bytes(),'Missing/stale Word add-in template'
    assert version.encode() in archive.read(prefix+'index.html'),'UI version mismatch'
    if tuple(int(part) for part in version.split('.')) >= (2,7,56):
        assert b'Nexus Paper Library' in archive.read(prefix+'index.html'),'NPL title missing'
        assert manifest['name']=='NPL Connector','Connector brand mismatch'
        asset_prefix=prefix.removesuffix('web/')+'assets/'
        assert archive.read(asset_prefix+'npl.png')==(ROOT/'assets'/'npl.png').read_bytes(),'Tray/application logo mismatch'
        if tuple(int(part) for part in version.split('.')) >= (2,7,57):
            assert archive.read(asset_prefix+'npl.ico')==(ROOT/'assets'/'npl.ico').read_bytes(),'Native window/shortcut ICO mismatch'
            assert b'<title>Nexus Paper Library</title>' in archive.read(prefix+'index.html'),'Window title suffix remains'
        for size in (16,32,48,128):
            name=f'browser-extension/icons/icon{size}.png'
            assert archive.read(name)==(ROOT/'extension'/'icons'/f'icon{size}.png').read_bytes(),'Connector logo mismatch'
        with zipfile.ZipFile(io.BytesIO(archive.read(word_template))) as template:
            ribbon=template.read('customUI/customUI.xml')
            assert b'label="NPL"' in ribbon,'Word ribbon brand mismatch'
    guide=next((ROOT/'docs').glob('*.txt'))
    assert archive.read(guide.name)==guide.read_bytes(),'Stale user guide'
    return count


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--version',default='2.5.2');args=parser.parse_args()
    version=args.version
    brand='NPL' if tuple(int(part) for part in version.split('.')) >= (2,7,56) else 'PaperWhale'
    portable=ROOT/'release'/f'{brand}-{version}-win-x64-portable.zip'
    setup=ROOT/'release'/f'{brand}-{version}-Setup.exe'
    connector=ROOT/'release'/f'{brand}-{version}-Connector.zip'
    with zipfile.ZipFile(portable) as archive:count=verify_archive(archive,True,version)
    print(f'PASS portable ZIP CRC, x64 executable, {count} matching web resources, all bundled skills, release docs, and no runtime/private data')
    installer=CArchiveReader(str(setup))
    assert installer.extract('version.txt').decode('utf-8-sig').strip()==version
    if brand=='NPL':
        logo_name=next((name for name in installer.toc if name.replace('\\','/')=='assets/npl.png'),None)
        assert logo_name,'Installer logo missing'
        assert installer.extract(logo_name)==(ROOT/'assets'/'npl.png').read_bytes(),'Installer logo mismatch'
    payload=installer.extract('PaperWhale.zip')
    dist_payload=ROOT/'dist'/'PaperWhale.zip'
    if dist_payload.exists():
        assert digest(payload)==digest(dist_payload.read_bytes()),'Installer payload is stale'
        print('PASS installer payload matches the current dist build')
    else:
        print('NOTE dist/PaperWhale.zip is absent; the embedded payload is checked against the source tree instead')
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:verify_archive(archive,False,version)
    print('PASS installer embedded payload, version, resource hashes and non-portable data mode')
    exe_path=ROOT/'dist'/'PaperWhale'/'PaperWhale.exe'
    if not exe_path.exists():
        # dist/ 被清理后仍可从便携包取同一份已编译程序做校验，避免把“没有 dist”误判成坏包。
        exe_path.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(portable) as archive:exe_path.write_bytes(archive.read('PaperWhale.exe'))
        print('NOTE dist executable is absent; using the executable embedded in the portable ZIP')
    app=CArchiveReader(str(exe_path)).open_embedded_archive('PYZ.pyz')
    required={'.'.join(path.relative_to(ROOT).with_suffix('').parts) for path in (ROOT/'paperwhale_server').rglob('*.py') if path.name!='__init__.py'}
    assert required.issubset(app.toc),'Missing backend modules: '+str(required-set(app.toc))
    assert version in app.extract('paperwhale_server.main').co_consts,'Compiled backend version mismatch'
    if brand=='NPL':
        assert 'Nexus Paper Library' in app.extract('paperwhale_server.main').co_consts,'Compiled brand mismatch'
    print(f'PASS compiled backend version and all {len(required)} backend modules')
    if tuple(int(part) for part in version.split('.')) >= (2,5,0):
        source_code=app.extract('paperwhale_server.services.discovery_sources')
        assert 'https://www.ebi.ac.uk/europepmc/webservices/rest/search' in source_code.co_consts,'Europe PMC adapter missing'
        functions={getattr(value,'co_name','') for value in source_code.co_consts}
        assert {'search_europepmc','_search_citations'}.issubset(functions),'Compiled search/citation tools missing'
        pipeline=app.extract('paperwhale_server.services.research_discovery')
        assert any(getattr(value,'co_name','')=='_structured' and 'task_prompt' in value.co_names for value in pipeline.co_consts),'Discovery skill is not wired to compiled pipeline'
        print('PASS compiled Europe PMC, citation tools and discovery skill integration')
    if tuple(int(part) for part in version.split('.')) >= (2,5,1):
        downloader=app.extract('paperwhale_server.services.pdf_download')
        assert 'pdf_domains' in downloader.co_names,'Compiled PDF policy is not imported'
        assert 'RequestPolicy' in app.extract('paperwhale_server.services.pdf_domains').co_names,'Compiled PDF throttle missing'
        assert any(getattr(value,'co_name','')=='request_referer' for value in app.extract('paperwhale_server.services.pdf_trace').co_consts),'Compiled request referrer policy missing'
        print('PASS compiled PDF domain coordination and browser referrer integration')
    if tuple(int(part) for part in version.split('.')) >= (2,5,2):
        downloader=app.extract('paperwhale_server.services.pdf_download')
        assert any(getattr(value,'co_name','')=='landing_links' and 'refresh_links' in value.co_varnames for value in downloader.co_consts),'Compiled HTML refresh tracking missing'
        fetcher=app.extract('paperwhale_server.services.pdf_fetch')
        assert 'sanitize_browser_attempts' in fetcher.co_names,'Compiled browser diagnostics missing'
        print('PASS compiled Elsevier refresh tracking and browser diagnostics')
    with zipfile.ZipFile(connector) as archive:
        assert archive.testzip() is None
        folder=brand+'-Connector/'
        manifest=json.loads(archive.read(folder+'manifest.json'))
        assert manifest['version']==version
        for source in (ROOT/'extension').rglob('*'):
            if source.is_file():assert archive.read(folder+source.relative_to(ROOT/'extension').as_posix())==source.read_bytes()
    print('PASS connector ZIP, version and source files')
    for line in (ROOT/'release'/'SHA256SUMS.txt').read_text(encoding='utf-8-sig').splitlines():
        expected,name=line.split('  ',1)
        assert digest((ROOT/'release'/name).read_bytes())==expected,'SHA256 mismatch: '+name
    print('PASS SHA256SUMS for every listed release file')
    for path in (setup,portable,connector):print(f'{path.name}: {path.stat().st_size} bytes / SHA256 {digest(path.read_bytes())}')


if __name__=='__main__':main()
