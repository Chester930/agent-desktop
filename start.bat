@echo off
echo Starting Agent Desktop...

:: Parse flags
set DEV_MODE=0
set DOCKER_MODE=0
set BUILD_MODE=0
for %%A in (%*) do (
  if /I "%%A"=="--dev"    set DEV_MODE=1
  if /I "%%A"=="--docker" set DOCKER_MODE=1
  if /I "%%A"=="--build"  set BUILD_MODE=1
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

  :: Wait for backend to be healthy
  echo Waiting for backend...
  :wait_backend
  docker inspect --format="{{.State.Health.Status}}" agent-desktop-backend-dev 2>nul | findstr /i "healthy" >nul
  if errorlevel 1 ( timeout /t 2 /nobreak >nul & goto wait_backend )

  echo.
  echo Frontend: http://localhost:4200 (Dev HMR)
  echo Backend:  see BACKEND_HOST_PORT in .env (default http://localhost:8760; only needed for direct API debugging, the app itself talks to the backend through the frontend proxy)
  echo.

  :: Launch Electron (Docker mode: skip local backend, load from port 4200)
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
  echo Backend:  http://localhost:8765  (由 Electron 自動啟動)
  echo Frontend: http://localhost:4200  [HMR enabled]
  echo.
  timeout /t 10 /nobreak >nul
  cd /d %~dp0 && node_modules\.bin\electron.cmd . --dev
  goto end
)

:: ── Default mode (local backend only) ─────────────────────────────────────────
echo.
echo Backend:  http://localhost:8765  (由 Electron 自動啟動)
echo.
echo Launching Electron...
cd /d %~dp0 && node_modules\.bin\electron.cmd .

:end
