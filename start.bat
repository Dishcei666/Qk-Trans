@echo off
rem Qk-Trans launcher: start local server and open the browser
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Not set up yet. Please run setup.bat first.
    pause
    exit /b 1
)

rem Already running? Just open the page instead of failing on the busy port.
netstat -ano | findstr /C:":8477" | findstr /C:"LISTENING" >nul 2>nul
if not errorlevel 1 (
    echo Qk-Trans is already running. Opening http://127.0.0.1:8477 ...
    start "" http://127.0.0.1:8477
    timeout /t 3 >nul
    exit /b 0
)

echo Starting Qk-Trans at http://127.0.0.1:8477 ...
echo Keep this window open while using the translator. Press Ctrl+C to quit.
".venv\Scripts\python.exe" -m app.main
pause
