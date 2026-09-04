$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "Launching Payment Pulse Services (Console + Customer Store)" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Start-Process cmd -ArgumentList "/c", "run_console.bat"
Start-Process cmd -ArgumentList "/c", "run_portal.bat"
Write-Host ""
Write-Host "Both servers launched!" -ForegroundColor Green
Write-Host "- SRE Operations Console: http://localhost:8000" -ForegroundColor Yellow
Write-Host "- Customer Sales Store:   http://localhost:8010" -ForegroundColor Yellow
