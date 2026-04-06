@echo off
echo ========================================
echo   Milwaukee AI Job Finder - Starting Up
echo ========================================
echo.

REM Load API_key.env if present (optional — keys can also be entered in the app Settings panel)
if exist API_key.env (
    for /f "tokens=1,2 delims==" %%a in (API_key.env) do (
        if not "%%a"=="" if not "%%b"=="" set %%a=%%b
    )
)

REM Check if venv exists
if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate venv and install requirements
call venv\Scripts\activate.bat

echo Installing dependencies...
pip install -r requirements.txt -q

echo.
echo Starting Milwaukee Job Finder...
echo Browser will open automatically.
echo Right-click the tray icon to control the app.
echo.
python main.py
