"""收集打包产物需要的第三方许可文本到 release/licenses/。"""
from __future__ import annotations

import importlib.metadata as md
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "release" / "licenses"

PACKAGES = [
    "fastapi",
    "starlette",
    "uvicorn",
    "pydantic",
    "pydantic-core",
    "httpx",
    "httpcore",
    "anyio",
    "sniffio",
    "idna",
    "certifi",
    "python-multipart",
    "bibtexparser",
    "pypdfium2",
    "pywebview",
    "pythonnet",
    "clr-loader",
]


def copy_special(name: str, dist) -> bool:
    """有些包（如 pypdfium2）的许可文件不叫 LICENSE，需要整目录复制。"""
    if name == "pypdfium2":
        source = Path(dist._path) / "licenses"
        if source.exists():
            shutil.copytree(source, OUT / name, dirs_exist_ok=True)
            return True
    return False


def copy_package(name: str) -> None:
    try:
        dist = md.distribution(name)
    except md.PackageNotFoundError:
        return
    target = OUT / name
    if copy_special(name, dist):
        return
    copied = False
    for file in dist.files or []:
        if "LICENSE" in file.name.upper() or "COPYING" in file.name.upper():
            source = Path(dist.locate_file(file))
            if source.exists() and source.is_file():
                target.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target / source.name)
                copied = True
    if not copied:
        print(f"  [warn] {name}: 未找到许可文件")


def copy_extra() -> None:
    pairs = [
        (ROOT / "web" / "vendor" / "echarts" / "LICENSE", "echarts/LICENSE"),
        (ROOT / "web" / "vendor" / "echarts" / "NOTICE", "echarts/NOTICE"),
        (ROOT / "web" / "vendor" / "mermaid" / "LICENSE", "mermaid/LICENSE"),
        (ROOT / "web" / "vendor" / "katex" / "LICENSE", "katex/LICENSE"),
        (ROOT / "web" / "vendor" / "pdfjs" / "LICENSE", "pdfjs/LICENSE"),
        (ROOT / "LICENSE", "PaperWhale/LICENSE"),
    ]
    for source, rel in pairs:
        if source.exists():
            dest = OUT / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
    # PyMuPDF 已移除；确认没有残留 AGPL 组件
    # Python 本体许可
    for candidate in (Path(sys.base_prefix) / "LICENSE.txt", Path(sys.base_prefix) / "LICENSE"):
        if candidate.exists():
            dest = OUT / "python" / candidate.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(candidate, dest)
            break


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True, exist_ok=True)
    for name in PACKAGES:
        copy_package(name)
    copy_extra()
    total = sum(1 for _ in OUT.rglob("*") if _.is_file())
    print(f"licenses copied: {total} files -> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
