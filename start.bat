@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
set "BACKEND_PY=%ROOT%.venv\Scripts\python.exe"
set "INFRA_MODE=managed"
set "BACKEND_PORT=8000"
set "FRONTEND_PORT=3000"

if exist "%ROOT%.env" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%ROOT%.env") do (
        if /i "%%A"=="HEALTHTRACE_INFRA_MODE" set "INFRA_MODE=%%B"
        if /i "%%A"=="HEALTHTRACE_BACKEND_PORT" set "BACKEND_PORT=%%B"
        if /i "%%A"=="HEALTHTRACE_FRONTEND_PORT" set "FRONTEND_PORT=%%B"
    )
)

rem Ports are interpolated into spawned command lines below; only plain digits
rem are accepted so a crafted .env value cannot become a command separator.
echo %BACKEND_PORT%|findstr /r "^[0-9][0-9]*$" >nul
if errorlevel 1 (
    echo [WARN] HEALTHTRACE_BACKEND_PORT is not numeric; falling back to 8000.
    set "BACKEND_PORT=8000"
)
echo %FRONTEND_PORT%|findstr /r "^[0-9][0-9]*$" >nul
if errorlevel 1 (
    echo [WARN] HEALTHTRACE_FRONTEND_PORT is not numeric; falling back to 3000.
    set "FRONTEND_PORT=3000"
)

echo ============================================
echo   HealthTrace - One Command Startup
echo   Infrastructure mode: %INFRA_MODE%
echo ============================================
echo.

if not exist "%BACKEND_PY%" (
    echo [ERROR] Python virtual environment not found:
    echo         %BACKEND_PY%
    echo Run: python -m venv .venv
    echo Then install: .venv\Scripts\python.exe -m pip install -e ".[dev]"
    pause
    exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
    echo [ERROR] npm command not found. Install Node.js or fix PATH.
    pause
    exit /b 1
)

cd /d "%ROOT%"

if /i "%INFRA_MODE%"=="managed" (
    where docker >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Docker command not found. Start or install Docker Desktop.
        pause
        exit /b 1
    )
    echo [1/4] Starting HealthTrace Docker services...
    docker compose up -d
    if errorlevel 1 (
        echo [WARN] Docker startup failed. Retrying once in 10 seconds...
        timeout /t 10 /nobreak >nul
        docker compose up -d
        if errorlevel 1 (
            echo [WARN] Infrastructure is unavailable. The API will start in degraded mode.
        )
    )
) else if /i "%INFRA_MODE%"=="external" (
    echo [1/4] External mode: using service URLs from .env; Docker Compose is unchanged.
) else (
    echo [ERROR] HEALTHTRACE_INFRA_MODE must be managed or external.
    pause
    exit /b 1
)

echo [2/4] Initializing database tables when PostgreSQL is reachable...
"%BACKEND_PY%" "%ROOT%scripts\init_db_safe.py"

set "BACKEND_STATE=free"
for /f %%i in ('powershell -NoProfile -Command "try { $h = Invoke-RestMethod -Uri 'http://127.0.0.1:%BACKEND_PORT%/health' -TimeoutSec 2; if ($h.service -eq 'HealthTrace') { 'healthtrace' } else { 'occupied' } } catch { 'free' }"') do set "BACKEND_STATE=%%i"
if /i "%BACKEND_STATE%"=="occupied" (
    set /a BACKEND_PORT+=1
    echo [WARN] Port 8000 belongs to another service; HealthTrace will use port %BACKEND_PORT%.
)

echo [3/4] Starting FastAPI backend: http://127.0.0.1:%BACKEND_PORT%
if /i "%BACKEND_STATE%"=="healthtrace" (
    echo [OK] HealthTrace backend is already running.
) else (
    start "HealthTrace Backend" cmd /k "cd /d ""%ROOT%"" && ""%BACKEND_PY%"" -m uvicorn backend.app:app --host 127.0.0.1 --port %BACKEND_PORT%"
)

set "FRONTEND_STATE=free"
for /f %%i in ('powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:%FRONTEND_PORT%' -TimeoutSec 2; if ($r.Content -match 'HealthTrace') { 'healthtrace' } else { 'occupied' } } catch { 'free' }"') do set "FRONTEND_STATE=%%i"
if /i "%FRONTEND_STATE%"=="occupied" (
    set /a FRONTEND_PORT+=1
    echo [WARN] Port 3000 belongs to another service; HealthTrace will use port %FRONTEND_PORT%.
)

echo [4/4] Starting Vue frontend: http://127.0.0.1:%FRONTEND_PORT%
if /i "%FRONTEND_STATE%"=="healthtrace" (
    echo [OK] HealthTrace frontend is already running.
) else (
    start "HealthTrace Frontend" cmd /k "cd /d ""%ROOT%frontend"" && set VITE_BACKEND_TARGET=http://127.0.0.1:%BACKEND_PORT% && npm run dev -- --port %FRONTEND_PORT% --strictPort"
)

timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:%FRONTEND_PORT%"

echo.
echo ============================================
echo   HealthTrace startup completed.
echo   Frontend:    http://127.0.0.1:%FRONTEND_PORT%
echo   Backend API: http://127.0.0.1:%BACKEND_PORT%
echo   API docs:    http://127.0.0.1:%BACKEND_PORT%/docs
echo.
echo   Optional services may degrade independently.
echo   Check detailed status at /health.
echo ============================================
echo.
pause
