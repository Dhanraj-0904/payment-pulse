@echo off
cd /d "%~dp0"
echo ========================================================
echo Starting Payment Pulse Customer Sales Store (Port 8010)
echo Open in browser: http://localhost:8010
echo ========================================================
set PYTHONPATH=backend;payment_portal;.
if exist "backend\.venv\Scripts\python.exe" (
    backend\.venv\Scripts\python.exe -m uvicorn payment_portal.backend.main:app --host 127.0.0.1 --port 8010
) else if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe -m uvicorn payment_portal.backend.main:app --host 127.0.0.1 --port 8010
) else (
    python -m uvicorn payment_portal.backend.main:app --host 127.0.0.1 --port 8010
)
pause
