"""NPL shell branding and safe shortcut migration, with no user desktop/registry writes."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("npl_setup_test", ROOT / "installer" / "setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class NplBrandingTests(unittest.TestCase):
    def test_shortcuts_use_independent_npl_ico_when_packaged(self):
        with tempfile.TemporaryDirectory(prefix='npl-shortcut-icon-') as folder:
            target = Path(folder)
            icon = target / '_internal' / 'assets' / 'npl.ico'
            icon.parent.mkdir(parents=True)
            icon.write_bytes((ROOT / 'assets' / 'npl.ico').read_bytes())
            with patch.object(setup, '_run_powershell') as run:
                setup.create_shortcuts(target / setup.EXE_NAME, target)
            script = run.call_args.args[0]
            self.assertIn('$s.IconLocation=' + setup._ps_quote(str(icon) + ',0'), script)
            self.assertNotIn('$s.IconLocation=' + setup._ps_quote(str(target / setup.EXE_NAME) + ',0'), script)

    def test_shortcut_ownership_and_collision_in_memory(self):
        # Mock the COM shell and filesystem in PowerShell itself. None of these
        # commands can touch real shortcuts; the helpers run on a dictionary.
        harness = r"""
$appDir='D:\Apps\NPL'; $appExe=$appDir+'\PaperWhale.exe'
$uninstallArgs='-NoProfile -ExecutionPolicy Bypass -File "D:\Apps\NPL\uninstall.ps1"'
$global:links=@{}
function Add-FakeLink($p,$target,$wd='',$argsText='') {
    $global:links[$p]=[pscustomobject]@{TargetPath=$target;WorkingDirectory=$wd;Arguments=$argsText}
}
Add-FakeLink 'D:\Fake\PaperWhale.lnk' $appExe
Add-FakeLink 'D:\Fake\Nexus Paper Library.lnk' 'D:\Other\other.exe'
Add-FakeLink 'D:\Fake\卸载 PaperWhale.lnk' 'C:\Windows\powershell.exe' $appDir $uninstallArgs
Add-FakeLink 'D:\Fake\unrelated.lnk' 'D:\Other\other.exe'
$fakeShell=[pscustomobject]@{}
$fakeShell | Add-Member ScriptMethod CreateShortcut {param($p) return $global:links[$p]}
function New-Object {param($ComObject) return $fakeShell}
function Test-Path {param($LiteralPath) return $global:links.ContainsKey($LiteralPath)}
function Get-ChildItem {param($LiteralPath,$Filter,$ErrorAction)
    @($global:links.Keys) | ForEach-Object { [pscustomobject]@{FullName=$_} }
}
function Remove-Item {param($LiteralPath,[switch]$Force) $global:links.Remove($LiteralPath)}
"""
        checks = r"""
$owned=Test-OwnedLink 'D:\Fake\PaperWhale.lnk'
$other=Test-OwnedLink 'D:\Fake\Nexus Paper Library.lnk'
$next=New-LinkPath 'D:\Fake' 'Nexus Paper Library'
Clear-OwnedLinks 'D:\Fake'
@{owned=$owned;other=$other;next=$next;remaining=@($global:links.Keys)} | ConvertTo-Json -Compress
"""
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", harness + setup.SHORTCUT_HELPERS + checks],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertTrue(result["owned"])
        self.assertFalse(result["other"])
        self.assertEqual(result["next"], r"D:\Fake\Nexus Paper Library (2).lnk")
        self.assertEqual(set(result["remaining"]), {r"D:\Fake\Nexus Paper Library.lnk", r"D:\Fake\unrelated.lnk"})

    def test_generated_scripts_parse_with_apostrophe_in_path(self):
        commands = []
        with tempfile.TemporaryDirectory(prefix="npl-brand-") as temp:
            target = Path(temp) / "O'Brien NPL"
            target.mkdir()
            with patch.object(setup, "_run_powershell", side_effect=commands.append):
                setup.write_uninstaller(target)
                setup.create_shortcuts(target / setup.EXE_NAME, target)
            scripts = commands + [(target / "uninstall.ps1").read_text(encoding="utf-8")]
            for i, source in enumerate(scripts):
                script = Path(temp) / f"parse-{i}.ps1"
                script.write_text(source, encoding="utf-8-sig")
                command = (
                    "$errors=$null;$tokens=$null;"
                    f"[void][System.Management.Automation.Language.Parser]::ParseFile({setup._ps_quote(script)},[ref]$tokens,[ref]$errors);"
                    "if($errors.Count){$errors | Out-String | Write-Output;exit 1}"
                )
                result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Nexus Paper Library", commands[-1])
        self.assertIn("O''Brien", commands[-1])
        self.assertEqual(setup.EXE_NAME, "PaperWhale.exe")
        self.assertTrue(setup.UNINSTALL_KEY.endswith("\\PaperWhale"))


if __name__ == "__main__":
    unittest.main()
