# Nexus Paper Library Windows build script
# Usage: powershell -ExecutionPolicy Bypass -File build.ps1 [-Version 2.10.1]
param([string]$Version = "2.10.1")

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Missing .venv. Run start.cmd first." }

$dist = Join-Path $PSScriptRoot "dist\PaperWhale"
$release = Join-Path $PSScriptRoot "release"
$payload = Join-Path $PSScriptRoot "dist\PaperWhale.zip"
New-Item -ItemType Directory -Force -Path $release | Out-Null

$guide = (Get-ChildItem "docs" -Filter "*.txt" | Select-Object -First 1).FullName
if (-not $guide) { throw "User guide (docs/*.txt) not found" }

Write-Host "[1/7] Icon"
& $python make_icon.py
if ($LASTEXITCODE -ne 0) { throw "Icon generation failed" }

Write-Host "[2/7] PyInstaller onedir ($Version)"
& $python -m PyInstaller --noconfirm --clean --windowed `
  --name PaperWhale `
  --icon "assets\npl.ico" `
  --add-data "assets\npl.png;assets" `
  --add-data "assets\npl.ico;assets" `
  --add-data "web;web" `
  --add-data "skills;skills" `
  --collect-all webview `
  --collect-all uvicorn `
  --collect-all pypdfium2 `
  --collect-all pystray `
  --collect-all PIL `
  --collect-all docx `
  --collect-all ddgs `
  --collect-all cryptography `
  --collect-all rookie_cookies `
  --collect-all psutil `
  --hidden-import paperwhale_server.main `
  --hidden-import paperwhale_server.config `
  --collect-submodules paperwhale_server `
  --collect-all paperwhale_server `
  --hidden-import webview.platforms.edgechromium `
  --exclude-module onnxruntime `
  --exclude-module numpy `
  --exclude-module scipy `
  --exclude-module matplotlib `
  --exclude-module pytest `
  desktop.py
if ($LASTEXITCODE -ne 0) { throw "Application build failed" }

Write-Host "[3/7] Collect third-party licenses"
& $python tools\collect_licenses.py
if ($LASTEXITCODE -ne 0) { throw "License collection failed" }

Write-Host "[4/7] Assemble package"
New-Item -ItemType Directory -Force -Path "$dist\licenses" | Out-Null
Copy-Item "LICENSE" "$dist\LICENSE" -Force
Copy-Item "THIRD-PARTY-NOTICES.md" "$dist\THIRD-PARTY-NOTICES.md" -Force
Copy-Item $guide "$dist" -Force
New-Item -ItemType Directory -Force -Path "$dist\docs" | Out-Null
$releaseDocs = Get-Content "tools\release-docs.json" -Encoding UTF8 | ConvertFrom-Json
foreach ($docName in $releaseDocs) {
  Copy-Item (Join-Path "docs" $docName) "$dist\docs" -Force
}
Copy-Item "$release\licenses\*" "$dist\licenses" -Recurse -Force
# 浏览器扩展与 Word 插件随安装包分发
Copy-Item "extension" "$dist\browser-extension" -Recurse -Force
New-Item -ItemType Directory -Force -Path "$dist\word" | Out-Null
Copy-Item "word\PaperWhaleMacros.dotm" "$dist\word\PaperWhaleMacros.dotm" -Force
Copy-Item "word\安装说明.txt" "$dist\word" -Force -ErrorAction SilentlyContinue

Write-Host "[5/7] Portable zip (portable.flag -> data next to exe)"
$portableZip = "$release\NPL-$Version-win-x64-portable.zip"
& $python tools\package_payload.py $dist $portableZip --portable
if ($LASTEXITCODE -ne 0) { throw "Portable archive failed" }

Write-Host "[6/7] Installer payload zip"
& $python tools\package_payload.py $dist $payload
if ($LASTEXITCODE -ne 0) { throw "Installer payload failed" }

Write-Host "[7/7] Build installer"
Set-Content -Path "installer\version.txt" -Value $Version -Encoding ascii -NoNewline
& $python -m PyInstaller --noconfirm --clean --windowed --onefile `
  --name "NPL-Setup" `
  --icon "assets\npl.ico" `
  --add-data "assets\npl.png;assets" `
  --add-data "$payload;." `
  --add-data "installer\version.txt;." `
  installer\setup.py
if ($LASTEXITCODE -ne 0) { throw "Installer build failed" }
Copy-Item "dist\NPL-Setup.exe" "$release\NPL-$Version-Setup.exe" -Force
Copy-Item $guide "$release" -Force
Copy-Item "THIRD-PARTY-NOTICES.md" "$release\THIRD-PARTY-NOTICES.md" -Force

Write-Host "[7b/7] Connector zip"
& $python tools\make_connector_zip.py "extension" "$release\NPL-$Version-Connector.zip"
if ($LASTEXITCODE -ne 0) { throw "Connector archive failed" }

Write-Host "SHA256"
$lines = @()
Get-ChildItem $release -File | Where-Object { $_.Name -ne "SHA256SUMS.txt" } | Sort-Object Name | ForEach-Object {
  $hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower()
  $lines += "$hash  $($_.Name)"
}
$lines | Set-Content "$release\SHA256SUMS.txt" -Encoding ascii
[System.IO.File]::WriteAllLines("$release\SHA256SUMS.txt", $lines, [System.Text.UTF8Encoding]::new($false))

Write-Host ""
Write-Host "Done ($Version):"
Get-ChildItem $release -File | Select-Object Name, @{n='MB';e={[math]::Round($_.Length/1MB,2)}} | Format-Table -AutoSize
