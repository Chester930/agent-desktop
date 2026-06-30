@echo off
echo Starting Claude Desktop...

:: Parse flags
set DEV_MODE=0
set DOCKER_MODE=0
set BUILD_MODE=0
for %%A in (%*) do (
  if /I "%%A"=="--dev"    set DEV_MODE=1
  if /I "%%A"=="--docker" set DOCKER_MODE=1
  if /I "%%A"=="--build"  set BUILD_MODE=1
)

:: ── Docker mode ───────────────────────────────────────────────────────────────
if "%DOCKER_MODE%"=="1" (
  echo [Docker] Starting backend + dev-frontend via Docker Compose [dev profile]...
  cd /d %~dp0
  if "%BUILD_MODE%"=="1" (
    docker compose --profile dev up -d --build
  ) else (
    docker compose --profile dev up -d
  )
  if errorlevel 1 (
    echo [Error] Docker Compose failed. Is Docker Desktop running?
    pause & exit /b 1
  )

  :: Wait for backend to be healthy
  echo Waiting for backend...
  :wait_backend
  docker inspect --format="{{.State.Health.Status}}" claude-desktop-backend-dev 2>nul | findstr /i "healthy" >nul
  if errorlevel 1 ( timeout /t 2 /nobreak >nul & goto wait_backend )

  echo.
  echo Backend:  http://localhost:8765
  echo Frontend: http://localhost:4200 (Dev HMR)
  echo.

  :: Launch Electron (Docker mode: skip local backend, load from port 4200)
  cd /d %~dp0 && node_modules\.bin\electron.cmd . --docker
  goto end
)

:: ── Dev mode ──────────────────────────────────────────────────────────────────
if "%DEV_MODE%"=="1" (
  set PYTHON=python
  where python >nul 2>&1 || (set PYTHON=python3)
  start "Claude Backend" cmd /k "cd /d %~dp0backend && "%PYTHON%" main.py"
  echo Starting Angular dev server with HMR...
  timeout /t 2 /nobreak >nul
  start "Angular Dev" cmd /k "cd /d %~dp0frontend && npm run start"
  echo.
  echo Backend:  http://localhost:8765
  echo Frontend: http://localhost:4200  [HMR enabled]
  echo.
  timeout /t 10 /nobreak >nul
  cd /d %~dp0 && node_modules\.bin\electron.cmd . --dev
  goto end
)

:: ── Default mode (local backend only) ─────────────────────────────────────────
set PYTHON=python
where python >nul 2>&1 || (set PYTHON=python3)
start "Claude Backend" cmd /k "cd /d %~dp0backend && "%PYTHON%" main.py"
echo.
echo Backend:  http://localhost:8765
echo.
timeout /t 3 /nobreak >nul
cd /d %~dp0 && node_modules\.bin\electron.cmd .

:end
