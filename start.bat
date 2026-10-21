@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
  set "PY=py -3"
) else (
  set "PY=python"
)

if not exist ".venv\Scripts\python.exe" (
  echo [FlipIntel] Creating Python virtual environment...
  %PY% -m venv .venv || goto :error
)

call ".venv\Scripts\activate.bat"
echo [FlipIntel] Installing Python dependencies...
python -m pip install --disable-pip-version-check -r requirements.txt || goto :error

where npm >nul 2>nul
if %errorlevel%==0 (
  if not exist "frontend\node_modules" (
    echo [FlipIntel] Installing dashboard dependencies...
    pushd frontend
    call npm install --no-audit --no-fund || (popd & goto :error)
    popd
  )
  if not exist "frontend\dist\index.html" (
    echo [FlipIntel] Building dashboard...
    pushd frontend
    call npm run build || (popd & goto :error)
    popd
  )
) else (
  if not exist "frontend\dist\index.html" (
    echo [FlipIntel] Node.js/npm was not found and the dashboard is not built.
    echo Install Node.js 20+ from https://nodejs.org, then run start.bat again.
    goto :error
  )
  echo [FlipIntel] npm not found; using the existing dashboard build.
)

echo.
echo [FlipIntel] Starting at http://127.0.0.1:8765
python run.py
goto :eof

:error
echo.
echo [FlipIntel] Startup failed. See the message above.
pause
exit /b 1
