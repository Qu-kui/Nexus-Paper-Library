"""把 customUI.xml 注入 .dotm，生成 NPL 功能区按钮。

Word 的兼容性问题（本仓库踩过）：功能区关系必须挂在**主文档部件**
（word/_rels/document.xml.rels）上；只挂包根 _rels/.rels 时 Word 会静默忽略
customUI，标签页不出现、onLoad 也不触发。这里两个位置都挂，保证 Word 认。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CUSTOM_UI = ROOT / "customUI.xml"
RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
#: 2006 版关系类型：本机 Zotero.dotm 用这个，Word 2021 认；2007 变体实测被忽略
UI_TYPE = "http://schemas.microsoft.com/office/2006/relationships/ui/extensibility"
#: 只挂包根 _rels/.rels（与 Zotero 的包结构一致；挂主文档部件反而会让 Word 忽略）
PKG_REL_TARGET = "customUI/customUI.xml"


def _rel_xml(target: str) -> str:
    return (
        f'<Relationship Id="rIdPaperWhaleUI" Type="{UI_TYPE}" '
        f'Target="{target}"/>'
    )


def _add_relationship(xml_text: str, target: str) -> str:
    if UI_TYPE in xml_text:
        return xml_text
    return xml_text.replace("</Relationships>", _rel_xml(target) + "</Relationships>")


def inject(dotm_path: Path) -> None:
    custom = CUSTOM_UI.read_bytes()
    fd, temp_name = tempfile.mkstemp(suffix=".dotm")
    os.close(fd)
    temp = Path(temp_name)
    with zipfile.ZipFile(dotm_path) as src, zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            if info.filename == "customUI/customUI.xml":
                continue
            data = src.read(info.filename)
            if info.filename == "word/_rels/document.xml.rels":
                # 老版本曾把关系挂在这里，清理掉，避免 Word 报结构问题
                text = data.decode("utf-8")
                for stale in ("2006/relationships/ui/extensibility", "2007/relationships/ui/extensibility"):
                    if stale in text:
                        import re as _re

                        text = _re.sub(
                            r'<Relationship[^>]*' + _re.escape(stale) + r'[^>]*/>', "", text
                        )
                data = text.encode("utf-8")
            elif info.filename == "_rels/.rels":
                data = _add_relationship(data.decode("utf-8"), PKG_REL_TARGET).encode("utf-8")
            elif info.filename == "[Content_Types].xml":
                text = data.decode("utf-8")
                if "/customUI/customUI.xml" not in text:
                    addition = (
                        '<Override PartName="/customUI/customUI.xml" '
                        'ContentType="application/xml"/>'
                    )
                    text = text.replace("</Types>", addition + "</Types>")
                data = text.encode("utf-8")
            dst.writestr(info, data)
        dst.writestr("customUI/customUI.xml", custom)
    shutil.move(temp, dotm_path)
    print(f"ribbon injected into {dotm_path}")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "PaperWhale.dotm"
    inject(target)
