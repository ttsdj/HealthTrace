@echo off
setlocal EnableExtensions
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python 3.12 was not found in PATH.
    exit /b 1
)
where npm >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Node.js and npm were not found in PATH.
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/5] Creating Python virtual environment...
    python -m venv .venv
    if errorlevel 1 exit /b 1
)

echo [2/5] Installing HealthTrace Python dependencies...
".venv\Scripts\python.exe" -m pip install -U pip
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pip install -e ".[dev]"
if errorlevel 1 exit /b 1

echo [3/5] Installing frontend dependencies...
call npm --prefix frontend ci
if errorlevel 1 exit /b 1

echo [4/5] Creating safe local environment configuration...
".venv\Scripts\python.exe" scripts\bootstrap_env.py
if errorlevel 1 exit /b 1

echo [5/5] Validating repository...
".venv\Scripts\python.exe" -m compileall -q backend scripts
if errorlevel 1 exit /b 1
call npm --prefix frontend run build
if errorlevel 1 exit /b 1

echo.
echo Setup completed. Fill in the LLM settings in .env, then run start.bat.
