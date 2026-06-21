@echo off
cd /d "%~dp0"
set "PYTHON=%LocalAppData%\Programs\Python\Python312\pythonw.exe"
if exist "%PYTHON%" (
  start "" "%PYTHON%" "%~dp0wealthfront_plaid_bridge.py"
) else (
  start "" pythonw "%~dp0wealthfront_plaid_bridge.py"
)
