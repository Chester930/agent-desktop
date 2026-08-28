@echo off
setlocal EnableExtensions EnableDelayedExpansion
echo Starting Agent Desktop...

:: Parse flags
set DEV_MODE=0
set DOCKER_MODE=0
set BUILD_MODE=0
set "FRONTEND_DEV_HOST_PORT=4201"
set "BACKEND_DEV_HOST_PORT=8761"
set "AGENT_DESKTOP_FRONTEND_URL="
set "AGENT_DESKTOP_BACKEND_URL="
for %%A in (%*) do (
  if /I "%%A"=="--dev"    set DEV_MODE=1
  if /I "%%A"=="--docker" set DOCKER_MODE=1
  if /I "%%A"=="--build"  set BUILD_MODE=1
)

:: Read development Docker ports from .env. The legacy names remain valid as
:: fallbacks so existing installations do not break when upgrading.
if exist "%~dp0.env" (
  for /f "usebackq tokens=1,* delims==" %%A in ("%~dp0.env") do (
    if /I "%%A"=="FRONTEND_HOST_PORT" set "FRONTEND_DEV_HOST_PORT=%%B"
    if /I "%%A"=="BACKEND_HOST_PORT"  set "BACKEND_DEV_HOST_PORT=%%B"
  )
  for /f "usebackq tokens=1,* delims==" %%A in ("%~dp0.env") do (
    if /I "%%A"=="FRONTEND_DEV_HOST_PORT" set "FRONTEND_DEV_HOST_PORT=%%B"
    if /I "%%A"=="BACKEND_DEV_HOST_PORT"  set "BACKEND_DEV_HOST_PORT=%%B"
  )
)

:: Resolve Python
set PYTHON=python
set HAS_PYTHON=1
where python >nul 2>&1 || (
  where python3 >nul 2>&1 && (set PYTHON=python3) || (set HAS_PYTHON=0)
)

:: Check if agency agents have been imported
if not exist "%USERPROFILE%\.claude\agency_imported.flag" (
  if "%HAS_PYTHON%"=="1" (
    echo ======================================================================
    echo  Do you want to import 140+ specialized agents and department teams 
    echo  from msitarzewski/agency-agents?
    echo ======================================================================
    set /p IMPORT_CHOICE="Import now? (y/n): "
    if /I "%IMPORT_CHOICE%"=="y" (
      echo [Import] Importing agency agents (this may take a minute)...
      "%PYTHON%" "%~dp0backend\agency_agents_importer.py"
    )
    echo.
  )
)

:: ── Docker mode ───────────────────────────────────────────────────────────────
if "%DOCKER_MODE%"=="1" (
  if not exist "%~dp0.env" (
    echo [Error] .env not found. Copy .env.example to .env and fill in CLAUDE_HOME first:
    echo   copy .env.example .env
    pause & exit /b 1
  )
  findstr /C:"你的名字" "%~dp0.env" >nul
  if not errorlevel 1 (
    echo [Error] .env still contains the placeholder "你的名字" in CLAUDE_HOME.
    echo Edit .env and set CLAUDE_HOME to your actual Windows user path.
    pause & exit /b 1
  )

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

  :: Wait for backend to be healthy, but fail with diagnostics instead of
  :: polling forever when Docker or the bind mounts are misconfigured.
  echo Waiting for backend...
  set /a WAIT_ATTEMPTS=0
  :wait_backend
  docker inspect --format="{{.State.Health.Status}}" agent-desktop-backend-dev 2>nul | findstr /i "healthy" >nul
  if not errorlevel 1 goto backend_ready
  set /a WAIT_ATTEMPTS+=1
  if !WAIT_ATTEMPTS! GEQ 60 (
    echo [Error] Backend did not become healthy within 120 seconds.
    docker compose --profile dev logs --tail 30 backend-dev
    pause & exit /b 1
  )
  timeout /t 2 /nobreak >nul
  goto wait_backend

  :backend_ready
  set "AGENT_DESKTOP_FRONTEND_URL=http://127.0.0.1:!FRONTEND_DEV_HOST_PORT!"
  set "AGENT_DESKTOP_BACKEND_URL=http://127.0.0.1:!BACKEND_DEV_HOST_PORT!"

  echo.
  echo Frontend: !AGENT_DESKTOP_FRONTEND_URL! (Dev HMR)
  echo Backend:  !AGENT_DESKTOP_BACKEND_URL! (direct API debugging only)
  echo.

  :: Launch Electron (Docker mode: skip local backend and use the same URLs above)
  cd /d %~dp0 && node_modules\.bin\electron.cmd . --docker
  goto end
)

:: ── Dev mode ──────────────────────────────────────────────────────────────────
:: 本機後端一律交給 Electron 的 startBackend()（electron/main.js）自動啟動，
:: 這裡不再重複 start 一個 python main.py——兩邊各自啟動一次會搶同一個
:: 8765 埠，其中一個必然綁定失敗，留下沒用的孤兒行程。
if "%DEV_MODE%"=="1" (
  echo Starting Angular dev server with HMR...
  start "Angular Dev" cmd /k "cd /d %~dp0frontend && npm run start"
  echo.
  echo Backend:  http://127.0.0.1:8765  (由 Electron 自動啟動)
  echo Frontend: http://127.0.0.1:4200  [HMR enabled]
  echo.
  timeout /t 10 /nobreak >nul
  cd /d %~dp0 && node_modules\.bin\electron.cmd . --dev
  goto end
)

:: ── Default mode (local backend only) ─────────────────────────────────────────
echo.
echo Backend:  http://127.0.0.1:8765  (由 Electron 自動啟動)
echo.
echo Launching Electron...
cd /d %~dp0 && node_modules\.bin\electron.cmd .

:end
endlocal
