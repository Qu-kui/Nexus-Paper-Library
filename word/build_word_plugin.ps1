# Build the Word add-in template from PaperWhale.bas (requires Microsoft Word).
# 1) open the minimal base.dotm container, replace the VBA module, save as PaperWhaleMacros.dotm
# 2) inject the ribbon (customUI.xml) into the package
# Output: word/PaperWhaleMacros.dotm (installer / desktop "install to Word" use this name)
# NOTE: this file is intentionally ASCII-only: PowerShell 5.1 mis-reads UTF-8 without BOM.
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$basPath = Join-Path $here "PaperWhale.bas"
$basePath = Join-Path $here "base.dotm"
$finalPath = Join-Path $here "PaperWhaleMacros.dotm"
$buildDirectory = Join-Path ([System.IO.Path]::GetTempPath()) ("NPL-WordBuild-" + [Guid]::NewGuid().ToString("N"))
[void](New-Item -ItemType Directory -Path $buildDirectory)
$outPath = Join-Path $buildDirectory "PaperWhaleMacros.dotm"
$logPath = Join-Path $here "build_word.log"
$python = Join-Path (Resolve-Path (Join-Path $here "..")).Path ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "python not found: $python" }

function Log([string]$message) {
    Add-Content -Path $logPath -Value ("{0} {1}" -f (Get-Date -Format "HH:mm:ss"), $message) -Encoding utf8
}

Set-Content -Path $logPath -Value "start" -Encoding utf8
Log "python: $python"

$code = [System.IO.File]::ReadAllText($basPath, [System.Text.Encoding]::UTF8)
# AddFromString does not accept the "Attribute VB_Name" line
$code = ($code -split "`r?`n" | Where-Object { $_ -notmatch '^Attribute VB_Name' }) -join "`r`n"
Log "source loaded: $($code.Length) chars"

if (-not (Test-Path $basePath)) {
    throw "base.dotm is missing; save an empty .dotm as the container first"
}
Copy-Item $basePath $outPath -Force
Log "base copied"

. (Join-Path $here "isolated_word.ps1")
$isolated = Start-IsolatedBuildWord
$word = $isolated.Application
Log "word started"
try {
    $doc = $word.Documents.Open($outPath, $false, $false, $false)
    $project = $doc.VBProject
    for ($i = $project.VBComponents.Count; $i -ge 1; $i--) {
        $component = $project.VBComponents.Item($i)
        if ($component.Type -eq 1 -or $component.Type -eq 2 -or $component.Type -eq 3) {
            $project.VBComponents.Remove($component)
        }
    }
    $module = $project.VBComponents.Add(1)
    $module.Name = "PaperWhale"
    $module.CodeModule.AddFromString($code)
    Log "code injected"

    # Citation picker: an empty UserForm plus its code module. Every control is created
    # at run time in UserForm_Initialize, so no binary designer data (.frx) is needed
    # and the layout stays in one text file.
    $formPath = Join-Path $here "PWCitationPicker.code.bas"
    if (-not (Test-Path $formPath)) { throw "missing $formPath" }
    $formCode = [System.IO.File]::ReadAllText($formPath, [System.Text.Encoding]::UTF8)
    $formCode = ($formCode -split "`r?`n" | Where-Object { $_ -notmatch '^Attribute VB_Name' }) -join "`r`n"
    try {
        $form = $project.VBComponents.Add(3)
        $form.Name = "PWCitationPicker"
        $form.Properties.Item("Width").Value = 880
        $form.Properties.Item("Height").Value = 540
        $form.Properties.Item("StartUpPosition").Value = 1
        $form.CodeModule.AddFromString($formCode)
        Log "picker form injected"
    } catch {
        Log ("ERR form: " + $_.Exception.Message)
        throw
    }

    # Style picker form (same text-only pattern as the citation picker)
    $stylePath = Join-Path $here "PWStylePicker.code.bas"
    if (-not (Test-Path $stylePath)) { throw "missing $stylePath" }
    $styleCode = [System.IO.File]::ReadAllText($stylePath, [System.Text.Encoding]::UTF8)
    $styleCode = ($styleCode -split "`r?`n" | Where-Object { $_ -notmatch '^Attribute VB_Name' }) -join "`r`n"
    try {
        $styleForm = $project.VBComponents.Add(3)
        $styleForm.Name = "PWStylePicker"
        $styleForm.Properties.Item("Width").Value = 460
        $styleForm.Properties.Item("Height").Value = 420
        $styleForm.Properties.Item("StartUpPosition").Value = 1
        $styleForm.CodeModule.AddFromString($styleCode)
        Log "style form injected"
    } catch {
        Log ("ERR style form: " + $_.Exception.Message)
        throw
    }

    $doc.Save()
    $doc.Close(0)
    # Reopen the serialized artifact and compare all three embedded modules.
    # This verifies the shipped binary, without executing macros or local APIs.
    $verified = $word.Documents.Open($outPath, $false, $true, $false)
    $expected = @{ PaperWhale = $code; PWCitationPicker = $formCode; PWStylePicker = $styleCode }
    foreach ($name in $expected.Keys) {
        $savedModule = $verified.VBProject.VBComponents.Item($name).CodeModule
        $savedCode = $savedModule.Lines(1, $savedModule.CountOfLines)
        # VBE normalizes the case of identifiers (for example Font.Name).
        if (($savedCode -replace "`r", '').Trim() -ine ($expected[$name] -replace "`r", '').Trim()) {
            [IO.File]::WriteAllText((Join-Path $buildDirectory ($name + '.actual.bas')), $savedCode)
            [IO.File]::WriteAllText((Join-Path $buildDirectory ($name + '.expected.bas')), $expected[$name])
            throw "saved VBA module differs from source: $name"
        }
    }
    $verified.Close(0)
    Log "serialized VBA modules verified"
    Log "saved: $((Get-Item $outPath).Length) bytes"
} catch {
    Log ("ERR: " + $_.Exception.Message)
    throw
} finally {
    $word.Quit(0)
    Log "quit"
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($word) | Out-Null
}

# Inject the ribbon. Start-Process because PS 5.1 sometimes loses the exit code of "&".
$injectLog = Join-Path $here "inject_ribbon.log"
$injectErr = Join-Path $here "inject_ribbon.err.log"
# Paths contain spaces ("Harness APP"); Start-Process does not quote them for us.
$injectArgs = @(
    '"' + (Join-Path $here "inject_ribbon.py") + '"',
    '"' + $outPath + '"'
)
$proc = Start-Process -FilePath $python -ArgumentList $injectArgs `
    -NoNewWindow -Wait -PassThru -RedirectStandardOutput $injectLog -RedirectStandardError $injectErr
Log "inject exit: $($proc.ExitCode)"
if ($proc.ExitCode -ne 0) {
    Log ("inject stdout: " + (Get-Content $injectLog -Raw -ErrorAction SilentlyContinue))
    Log ("inject stderr: " + (Get-Content $injectErr -Raw -ErrorAction SilentlyContinue))
    throw "ribbon injection failed"
}
Log "ribbon injected: $((Get-Item $outPath).Length) bytes"
Copy-Item -LiteralPath $outPath -Destination $finalPath -Force
# Only these known files in the unique temporary build directory are removed.
Remove-Item -LiteralPath $outPath -Force
Remove-Item -LiteralPath $buildDirectory -Force
Write-Host "built $finalPath"
