"""把 extension/ 打成浏览器可解压安装的 Connector zip。"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path


def main() -> int:
    source = Path(sys.argv[1])
    target = Path(sys.argv[2])
    if not source.exists():
        print(f"missing: {source}")
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in source.rglob("*"):
            if path.is_file():
                arcname = "NPL-Connector/" + path.relative_to(source).as_posix()
                archive.write(path, arcname)
    print(f"connector zip: {target} ({target.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
