@echo off
cd /d "%~dp0"
set "PYTHON=%LocalAppData%\Programs\Python\Python312\python.exe"
if exist "%PYTHON%" (
  "%PYTHON%" "%~dp0wealthfront_sync.py"
) else (
  python "%~dp0wealthfront_sync.py"
)
