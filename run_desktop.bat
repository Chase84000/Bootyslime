@echo off
cd /d "%~dp0"
set "PYTHON=%LocalAppData%\Programs\Python\Python312\pythonw.exe"
if exist "%PYTHON%" (
  "%PYTHON%" finance_lens_qt.py
) else (
  python finance_lens_qt.py
)
