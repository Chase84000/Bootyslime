$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\pythonw.exe"
if (Test-Path $pythonw) {
    Start-Process -FilePath $pythonw -ArgumentList @((Join-Path $root "finance_lens_qt.py")) -WorkingDirectory $root
} else {
    Start-Process -FilePath "python" -ArgumentList @((Join-Path $root "finance_lens_qt.py")) -WorkingDirectory $root
}
