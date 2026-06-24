@echo off
echo Starting Claude Desktop...

:: Auto-detect Python
set PYTHON=python
where python >nul 2>&1 || (set PYTHON=python3)

:: Start Python backend
start "Claude Backend" cmd /k "cd /d %~dp0backend && "%PYTHON%" main.py"

:: Wait 2 seconds then start Angular dev server (dev mode only)
timeout /t 2 /nobreak >nul

echo.
echo Backend:  http://localhost:8765
echo.
echo Backend started. Close the backend window to stop.
