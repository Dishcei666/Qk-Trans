@echo off
rem Qk-Trans first-time setup: venv + dependencies + model download (needs internet, one time only)
setlocal
cd /d "%~dp0"

set PYCMD=py -3
where py >nul 2>nul
if errorlevel 1 set PYCMD=python

echo [1/3] Creating virtual environment...
if not exist ".venv" (
    %PYCMD% -m venv .venv
    if errorlevel 1 (
        echo FAILED to create venv. Please install Python 3.10+ from https://www.python.org/downloads/
        pause
        exit /b 1
    )
)
call ".venv\Scripts\activate.bat"

echo [2/3] Installing dependencies (one time, needs internet)...
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
    echo FAILED to install dependencies.
    echo If the download is slow, edit this file and add: -i https://pypi.tuna.tsinghua.edu.cn/simple
    pause
    exit /b 1
)

echo [3/3] Downloading and converting translation models (about 700 MB)...
python scripts\download_models.py
if errorlevel 1 (
    echo FAILED to download models.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Setup complete! Double-click start.bat
echo  to launch the translator.
echo ============================================
pause
