param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"

Write-Host "Checking Python..."
try {
    & $Python --version
} catch {
    Write-Error "No working Python interpreter was found. Install Python 3.12+ and re-run this script."
    exit 1
}

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..."
    & $Python -m venv .venv
}

Write-Host "Upgrading pip..."
& .\.venv\Scripts\python.exe -m pip install --upgrade pip

Write-Host "Done. Activate it with: .\.venv\Scripts\Activate.ps1"
