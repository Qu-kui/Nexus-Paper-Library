@echo off
setlocal
cd /d "%~dp0"

echo [1/3] Staging changes...
git add -A
git diff --cached --quiet
if errorlevel 1 (
  echo [2/3] Committing...
  git commit -m "Sync %date% %time%"
) else (
  echo [2/3] No local changes to commit.
)

echo [3/3] Pushing to GitHub...
git push origin main
if errorlevel 1 (
  echo.
  echo Push failed. Check network and SSH access, then run this file again.
  if /I not "%~1"=="/auto" pause
  exit /b 1
)

echo.
echo Synced: https://github.com/Qu-kui/Nexus-Paper-Library
if /I not "%~1"=="/auto" pause
