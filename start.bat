@echo off
setlocal

set "ROOT=%~dp0"
set "BACKEND_PY=%ROOT%.venv\Scripts\python.exe"

echo ============================================
echo   MedRetrieveV2.0 - One Command Startup
echo ============================================
echo.

if not exist "%BACKEND_PY%" (
    echo [ERROR] Python venv not found:
    echo         %BACKEND_PY%
    echo Please create/install the project virtual environment first.
    pause
    exit /b 1
)

where docker >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Docker command not found. Please start/install Docker Desktop.
    pause
    exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
    echo [ERROR] npm command not found. Please install Node.js or fix PATH.
    pause
    exit /b 1
)

echo [1/4] Starting Docker services...
cd /d "%ROOT%"

rem V3 reuses the MedRetrieveV2.0 infrastructure containers when they already exist.
rem This avoids Docker name conflicts such as /medretrieve-v2-milvus-etcd already in use.
docker start medretrieve-v2-postgres medretrieve-v2-redis medretrieve-v2-milvus-etcd medretrieve-v2-milvus-minio medretrieve-v2-milvus-standalone medretrieve-v2-milvus-attu >nul 2>nul

set "POSTGRES_RUNNING=false"
set "REDIS_RUNNING=false"
set "MILVUS_RUNNING=false"
for /f %%i in ('docker inspect -f "{{.State.Running}}" medretrieve-v2-postgres 2^>nul') do set "POSTGRES_RUNNING=%%i"
for /f %%i in ('docker inspect -f "{{.State.Running}}" medretrieve-v2-redis 2^>nul') do set "REDIS_RUNNING=%%i"
for /f %%i in ('docker inspect -f "{{.State.Running}}" medretrieve-v2-milvus-standalone 2^>nul') do set "MILVUS_RUNNING=%%i"

if /i "%POSTGRES_RUNNING%"=="true" if /i "%REDIS_RUNNING%"=="true" if /i "%MILVUS_RUNNING%"=="true" (
    echo [OK] Reusing existing medretrieve-v2 Docker services.
) else (
    docker compose up -d
    if errorlevel 1 (
        echo [WARN] docker compose up failed. Waiting 10 seconds and retrying once...
        timeout /t 10 /nobreak >nul
        docker compose up -d
        if errorlevel 1 (
            echo [WARN] Docker services are unavailable. Backend will still start in degraded mode.
            echo        If another MedRetrieve workspace is already running, this V3 workspace can reuse it.
            echo        Otherwise start Docker Desktop later and run: docker compose up -d
        )
    )
)

echo [2/4] Initializing database tables...
"%BACKEND_PY%" "%ROOT%scripts\init_db_safe.py"

echo [3/4] Starting FastAPI backend: http://127.0.0.1:8000
set "BACKEND_READY=false"
for /f %%i in ('powershell -NoProfile -Command "try { ((Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2).StatusCode -eq 200).ToString().ToLower() } catch { 'false' }"') do set "BACKEND_READY=%%i"
if /i "%BACKEND_READY%"=="true" (
    echo [OK] Backend is already running. Reusing http://127.0.0.1:8000
) else (
    start "MedRetrieve Backend" cmd /k "cd /d ""%ROOT%"" && ""%BACKEND_PY%"" -m uvicorn backend.app:app --host 127.0.0.1 --port 8000"
)

echo [4/4] Starting Vue frontend: http://localhost:3000
set "FRONTEND_READY=false"
for /f %%i in ('powershell -NoProfile -Command "try { ((Invoke-WebRequest -UseBasicParsing -Uri 'http://localhost:3000' -TimeoutSec 2).StatusCode -lt 500).ToString().ToLower() } catch { 'false' }"') do set "FRONTEND_READY=%%i"
if /i "%FRONTEND_READY%"=="true" (
    echo [OK] Frontend is already running. Reusing http://localhost:3000
) else (
    start "MedRetrieve Frontend" cmd /k "cd /d ""%ROOT%frontend"" && npm run dev"
)

timeout /t 3 /nobreak >nul
start "" "http://localhost:3000"

echo.
echo ============================================
echo   Startup complete.
echo   Frontend: http://localhost:3000
echo   Backend docs: http://127.0.0.1:8000/docs
echo.
echo   If Neo4j is not running, KG will show degraded,
echo   but normal RAG and LLM chat can still work.
echo ============================================
echo.
echo You can close this window. Backend/frontend run in separate windows.
pause
