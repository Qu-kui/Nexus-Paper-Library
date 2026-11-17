# -*- coding: utf-8 -*-
"""Word 插件模板的包结构回归。

背景（踩过的坑）：
- 早前的 dotm 只注入了关系、没有功能区，Word 里什么都没有；
- 功能区关系必须挂在包根 `_rels/.rels`，类型用 2006 版（本机 Zotero.dotm 同款）；
  挂到 word/_rels/document.xml.rels 或 2007 版类型，Word 2021 会静默忽略。
"""
from __future__ import annotations

import re
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORD = ROOT / "word"
DOTM = WORD / "PaperWhaleMacros.dotm"
BAS = WORD / "PaperWhale.bas"
CUSTOM_UI = WORD / "customUI.xml"
UI_TYPE_2006 = "http://schemas.microsoft.com/office/2006/relationships/ui/extensibility"


class WordPluginTemplateTests(unittest.TestCase):
    def test_shipped_ribbon_matches_npl_source_without_duplicate_parts(self):
        with zipfile.ZipFile(DOTM) as archive:
            self.assertEqual(archive.namelist().count("customUI/customUI.xml"), 1)
            self.assertEqual(archive.read("customUI/customUI.xml"), CUSTOM_UI.read_bytes())
            ribbon = archive.read("customUI/customUI.xml").decode("utf-8")
        self.assertIn('id="PaperWhaleTab" label="NPL"', ribbon)
        self.assertNotIn('label="PaperWhale"', ribbon)

    def test_visible_brand_and_existing_preferences_are_separate(self):
        bas = BAS.read_text(encoding="utf-8")
        self.assertIn('PW_APP As String = "NPL"', bas)
        self.assertIn('PW_SETTINGS_APP As String = "PaperWhale"', bas)
        self.assertIn('GetSetting(PW_SETTINGS_APP, "Config", "Port"', bas)
        self.assertIn('SaveSetting PW_SETTINGS_APP, "Config", "Style"', bas)
        self.assertNotIn('SaveSetting PW_APP,', bas)

    def test_dotm_is_a_valid_package_with_vba_and_ribbon(self):
        self.assertTrue(DOTM.is_file(), "缺少编译好的 Word 模板")
        with zipfile.ZipFile(DOTM) as archive:
            names = set(archive.namelist())
            for required in (
                "[Content_Types].xml",
                "_rels/.rels",
                "word/document.xml",
                "word/vbaProject.bin",
                "customUI/customUI.xml",
            ):
                self.assertIn(required, names, f"dotm 缺少 {required}")
            rels = archive.read("_rels/.rels").decode("utf-8")
            self.assertIn(UI_TYPE_2006, rels, "功能区关系必须用 2006 版类型")
            self.assertIn('Target="customUI/customUI.xml"', rels)
            content_types = archive.read("[Content_Types].xml").decode("utf-8")
            self.assertIn("/customUI/customUI.xml", content_types)
            ribbon = archive.read("customUI/customUI.xml").decode("utf-8")
            self.assertIn('onLoad="PWOnLoad"', ribbon)
            self.assertIn("PWBtnInsertCitation", ribbon)
            self.assertIn("PWBtnInsertBibliography", ribbon)

    def test_ribbon_relationships_are_not_put_on_the_document_part(self):
        with zipfile.ZipFile(DOTM) as archive:
            doc_rels = archive.read("word/_rels/document.xml.rels").decode("utf-8")
        self.assertNotIn(
            "relationships/ui/extensibility",
            doc_rels,
            "Word 会把主文档部件上的功能区关系当成坏结构",
        )

    def test_every_ribbon_action_exists_in_the_vba_source(self):
        ribbon = CUSTOM_UI.read_text(encoding="utf-8")
        bas = BAS.read_text(encoding="utf-8")
        actions = set(re.findall(r'onAction="([^"]+)"', ribbon))
        self.assertTrue(actions, "customUI 里没有 onAction")
        for action in sorted(actions):
            self.assertRegex(
                bas,
                rf"Public\s+Sub\s+{re.escape(action)}\s*\(",
                f"功能区按钮 {action} 在 PaperWhale.bas 里没有对应宏",
            )

    def test_vba_source_uses_local_api_and_bookmarks(self):
        bas = BAS.read_text(encoding="utf-8")
        self.assertIn("Option Explicit", bas)
        self.assertIn("127.0.0.1", bas)
        self.assertIn("/api/cite/document.txt", bas)
        self.assertIn("--BIBLIOGRAPHY--", bas)
        self.assertIn("ActiveDocument.Bookmarks", bas)
        # 引文格式列表必须从服务端动态拉（自定义格式要能出现在 Word 里）
        self.assertIn("/api/cite/styles.txt", bas)
        self.assertIn("/api/cite/document.txt", bas)
        # 不该拉外部程序或访问外网
        self.assertNotIn("WScript.Shell", bas)
        for host in re.findall(r"https?://([^/\"]+)", bas):
            self.assertIn(host.split(":")[0], {"127.0.0.1", "localhost"}, host)

    def test_build_and_installer_ship_the_template(self):
        build = (ROOT / "build.ps1").read_text(encoding="utf-8")
        setup = (ROOT / "installer" / "setup.py").read_text(encoding="utf-8")
        self.assertIn('Copy-Item "word\\PaperWhaleMacros.dotm"', build)
        self.assertNotIn("word plugin disabled", build)
        self.assertIn("install_word_plugin(target)", setup)
        self.assertIn("PaperWhaleMacros.dotm", setup)


if __name__ == "__main__":
    unittest.main()
