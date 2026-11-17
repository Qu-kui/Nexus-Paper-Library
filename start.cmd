@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [PaperWhale] First run: creating environment, please wait...
  python -m venv .venv
  ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt -q
)
".venv\Scripts\python.exe" run.py
if errorlevel 1 pause