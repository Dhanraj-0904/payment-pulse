@echo off
cd /d "%~dp0"
echo ========================================================
echo Launching Payment Pulse Services (Console + Customer Store)
echo ========================================================
start "Payment Pulse - SRE Console (Port 8000)" cmd /c "run_console.bat"
start "Payment Pulse - Customer Store (Port 8010)" cmd /c "run_portal.bat"
echo.
echo Both servers have been launched in separate terminal windows!
echo - SRE Operations Console: http://localhost:8000
echo - Customer Sales Store:   http://localhost:8010
echo.
pause
