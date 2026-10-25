@echo off
setlocal
title SORA ERP

set "PROJECT=%~dp0"
set "PYTHON=%PROJECT%..\.venv\Scripts\python.exe"
set "PG_BIN=C:\Program Files\PostgreSQL\18\bin"
set "PGDATA=%LOCALAPPDATA%\SoraERP\pgdata"
set "PGLOG=%LOCALAPPDATA%\SoraERP\postgres.log"
set "PYTHONPATH=%PROJECT%"

if not exist "%PYTHON%" (
    echo Python virtual environment not found at "%PYTHON%".
    echo Create it with:
    echo     py -m venv ..\.venv
    echo     ..\.venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

if not exist "%PGDATA%" (
    echo PostgreSQL data directory not found at "%PGDATA%".
    echo See docs\SETUP.md for first-time database setup.
    pause
    exit /b 1
)

"%PG_BIN%\pg_isready.exe" -h 127.0.0.1 -p 5433 >nul 2>&1
if errorlevel 1 (
    echo Starting PostgreSQL on port 5433...
    "%PG_BIN%\pg_ctl.exe" -D "%PGDATA%" -l "%PGLOG%" -o "-p 5433" -w start
    if errorlevel 1 (
        echo Failed to start PostgreSQL. See "%PGLOG%".
        pause
        exit /b 1
    )
) else (
    echo PostgreSQL is already running on port 5433.
)

cd /d "%PROJECT%"
echo.
echo SORA ERP is starting at http://127.0.0.1:8000
echo Sign in with admin@sora.com / admin123
echo Press Ctrl+C to stop the server.
echo.
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:8000/login'"
"%PYTHON%" manage.py runserver 127.0.0.1:8000

pause
