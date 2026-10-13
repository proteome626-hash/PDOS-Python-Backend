@echo off
setlocal enabledelayedexpansion
title PDOS Backend - Windows Runner
cd /d "%~dp0"

echo ============================================================
echo           PDOS Backend - Windows Runner
echo ============================================================
echo.

:: 1. Check Python installation
where python >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Python is not installed or not in your PATH!
    echo Please install Python 3.10+ from https://www.python.org/
    pause
    exit /b 1
)

:: 2. Check and generate .env if missing
if not exist ".env" (
    echo [*] Generating secure .env file for Windows...
    python -c "import secrets; print('SECRET_KEY=' + secrets.token_hex(32))" > .env.tmp
    echo DATABASE_URL=sqlite:///./pdos.db>> .env.tmp
    echo CORS_ORIGINS=*>> .env.tmp
    echo UVICORN_WORKERS=1>> .env.tmp
    echo LOG_LEVEL=info>> .env.tmp
    move /y .env.tmp .env >nul
    echo [OK] Created .env with random SECRET_KEY
)

:: 3. Create upload folders if missing
if not exist "uploads" mkdir uploads
if not exist "uploads\products" mkdir uploads\products
if not exist "uploads\signatures" mkdir uploads\signatures
if not exist "uploads\receipts" mkdir uploads\receipts
if not exist "uploads\visits" mkdir uploads\visits
if not exist "uploads\profiles" mkdir uploads\profiles

:: 4. Check dependencies
python -c "import fastapi, uvicorn, sqlalchemy, pydantic" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [*] Installing required Python packages...
    pip install -r requirements.txt
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Failed to install packages.
        pause
        exit /b 1
    )
)

:: 5. Detect local Wi-Fi / LAN IP
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4 Address" /c:"Adresse IPv4"') do (
    set "RAW_IP=%%a"
    set "IP=!RAW_IP: =!"
    if not "!IP!"=="" (
        if not "!IP:~0,3!"=="127" (
            if not "!IP:~0,7!"=="169.254" (
                set "LOCAL_IP=!IP!"
            )
        )
    )
)

echo.
echo ============================================================
echo [OK] Starting PDOS FastAPI Backend Server!
echo ============================================================
echo   Local URL:    http://127.0.0.1:8000
if defined LOCAL_IP (
echo   Network URL:  http://!LOCAL_IP!:8000
)
echo   Swagger Docs: http://127.0.0.1:8000/docs
echo ============================================================
echo Press Ctrl + C to stop the server.
echo.

uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause
