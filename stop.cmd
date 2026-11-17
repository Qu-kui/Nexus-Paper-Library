@echo off
setlocal
cd /d "%~dp0"
echo [PaperWhale] Stopping service on port 8765...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8765" ^| findstr "LISTENING"') do (
  taskkill /F /PID %%a >nul 2>nul
)
echo [PaperWhale] Stopped.