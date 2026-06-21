$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$server = Join-Path $root "bridge-server.js"
$out = Join-Path $env:TEMP "finance-lens.out.log"
$err = Join-Path $env:TEMP "finance-lens.err.log"

Start-Process -FilePath "node.exe" -ArgumentList @($server) -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err
Start-Sleep -Seconds 1
Start-Process "http://127.0.0.1:8787"
