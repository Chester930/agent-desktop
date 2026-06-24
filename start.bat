@echo off
echo Starting Claude Desktop...

:: Auto-detect Python
set PYTHON=python
where python >nul 2>&1 || (set PYTHON=python3)

:: Start Python backend
start "Claude Backend" cmd /k "cd /d %~dp0backend && "%PYTHON%" main.py"

:: Check for --dev flag
set DEV_MODE=0
for %%A in (%*) do (
  if /I "%%A"=="--dev" set DEV_MODE=1
)

if "%DEV_MODE%"=="1" (
  echo Starting Angular dev server with HMR...
  timeout /t 2 /nobreak >nul
  start "Angular Dev" cmd /k "cd /d %~dp0frontend && npm run start"
  echo.
  echo Backend:  http://localhost:8765
  echo Frontend: http://localhost:4200  [HMR enabled]
  echo.
  echo Run electron after Angular finishes loading...
  timeout /t 10 /nobreak >nul
  cd /d %~dp0 && node_modules\.bin\electron.cmd . --dev
) else (
  echo.
  echo Backend:  http://localhost:8765
  echo.
  echo Backend started. Run ^"npm run electron^" or double-click the Electron binary to launch UI.
)
