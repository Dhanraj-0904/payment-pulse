@echo off
cd /d "%~dp0"
echo ========================================================
echo Starting Payment Pulse SRE Operations Console (Port 8000)
echo Open in browser: http://localhost:8000
echo ========================================================
set PYTHONPATH=backend;.
if exist "backend\.venv\Scripts\python.exe" (
    backend\.venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
) else if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python.exe -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
) else (
    python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
)
pause
