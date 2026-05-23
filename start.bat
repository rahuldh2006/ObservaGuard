@echo off
setlocal enabledelayedexpansion
REM ObservaGuard — Quick Start for Windows

REM Always run from the directory containing this script
cd /d "%~dp0"

echo.
echo ==========================================
echo   ObservaGuard — SRE Watchdog
echo ==========================================

echo.
echo [1/3] Installing Python dependencies...
pip install -r requirements.txt
echo       Dependencies installed.

echo.
echo [2/3] Selecting port and starting server...

REM --- Port selection: prefer 8080, fall back to 8081 ---
set PORT=8080

REM Kill any process already on 8080
for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":8080 " ^| findstr "LISTENING"') do (
    echo   Stopping process on port 8080 (PID %%p^)...
    taskkill /F /PID %%p >nul 2>&1
)
timeout /t 1 /nobreak >nul

REM If 8080 is still occupied (Windows-reserved or unkillable), switch to 8081
netstat -ano 2>nul | findstr ":8080 " | findstr "LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo   Port 8080 still in use -- falling back to 8081...
    set PORT=8081
    for /f "tokens=5" %%p in ('netstat -ano 2^>nul ^| findstr ":8081 " ^| findstr "LISTENING"') do (
        echo   Stopping process on port 8081 (PID %%p^)...
        taskkill /F /PID %%p >nul 2>&1
    )
    timeout /t 1 /nobreak >nul
)

echo   Using port !PORT!
echo       Dashboard: http://localhost:!PORT!/dashboard
echo       API Docs:  http://localhost:!PORT!/docs
echo.

REM Use "python -m uvicorn" so it works even if the Scripts dir is not in PATH
start "ObservaGuard Server" cmd /k "cd /d %~dp0 && python -m uvicorn main:app --host 0.0.0.0 --port !PORT! --reload"

REM Poll until the server answers (up to 20 s, 1 s intervals)
echo Waiting for server to start...
set READY=0
for /L %%i in (1,1,20) do (
    if !READY! EQU 0 (
        python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:!PORT!/health', timeout=2)" >nul 2>&1
        if not errorlevel 1 (
            set READY=1
            echo   Server is ready^^!
        ) else (
            timeout /t 1 /nobreak >nul
        )
    )
)
if !READY! EQU 0 (
    echo   WARNING: Server did not respond in 20 s. Check the server window for errors.
    pause
    exit /b 1
)

echo.
echo [3/3] Running demo simulation...
python simulator.py --scenario demo --port !PORT!

echo.
echo ==========================================
echo   ObservaGuard is running!
echo   Dashboard: http://localhost:!PORT!/dashboard
echo   Alerts:    http://localhost:!PORT!/alerts
echo   API Docs:  http://localhost:!PORT!/docs
echo ==========================================
pause
