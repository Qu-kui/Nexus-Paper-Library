"""Create distribution archives without copying internal working documents."""
from __future__ import annotations
import argparse
import json
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def package(source:Path,target:Path,portable:bool=False):
    source=source.resolve();target=target.resolve()
    if source!=ROOT/'dist'/'PaperWhale' or target.parent not in {ROOT/'release',ROOT/'dist'}:
        raise ValueError('Only the generated distribution and release folders are supported')
    docs=json.loads((ROOT/'tools'/'release-docs.json').read_text(encoding='utf-8'))
    if not(source/'PaperWhale.exe').is_file():raise ValueError('Compiled application missing')
    with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for path in source.rglob('*'):
            if not path.is_file():continue
            relative=path.relative_to(source)
            if relative.parts[0]=='docs' or relative.as_posix()=='portable.flag':continue
            archive.write(path,relative.as_posix())
        for name in docs:
            path=ROOT/'docs'/name
            if path.parent!=ROOT/'docs' or not path.is_file():raise ValueError('Missing release documentation')
            archive.write(path,'docs/'+name)
        if portable:archive.writestr('portable.flag',b'')
    print(f'Packaged {target.name}: {target.stat().st_size} bytes')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('source',type=Path);parser.add_argument('target',type=Path);parser.add_argument('--portable',action='store_true')
    args=parser.parse_args();package(args.source,args.target,args.portable)
