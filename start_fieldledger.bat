@echo off
rem FieldLedger - one-click local start for Windows 10/11.
rem Creates .venv on first run, installs the web dependencies, builds the UI if needed,
rem starts the server on 127.0.0.1:8765 and opens the browser. Close this window to stop.
setlocal
cd /d "%~dp0"
set "PORT=8765"

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment in .venv ...
  py -3.13 -m venv .venv 2>nul
  if errorlevel 1 py -3 -m venv .venv 2>nul
  if errorlevel 1 python -m venv .venv
  if errorlevel 1 goto :nopython
)

echo Installing FieldLedger and web dependencies ...
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -e ".[web]"
if errorlevel 1 goto :pipfail

if not exist "src\fieldledger\web\static\index.html" (
  where npm >nul 2>nul
  if errorlevel 1 goto :nostatic
  echo Building the web UI ...
  pushd frontend
  call npm ci
  if errorlevel 1 ( popd & goto :npmfail )
  call npm run build
  if errorlevel 1 ( popd & goto :npmfail )
  popd
)

echo.
echo Starting FieldLedger at http://127.0.0.1:%PORT%  (Ctrl+C to stop)
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:%PORT%"
".venv\Scripts\python.exe" -m fieldledger.web --port %PORT%
goto :eof

:nopython
echo ERROR: Python 3.13 was not found. Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
pause
exit /b 1
:pipfail
echo ERROR: installing the Python dependencies failed. Check your internet connection and try again.
pause
exit /b 1
:nostatic
echo ERROR: the built UI (src\fieldledger\web\static) is missing and Node.js was not found.
echo Install Node.js 24 from https://nodejs.org/ and run this script again.
pause
exit /b 1
:npmfail
echo ERROR: building the web UI failed. See the messages above.
pause
exit /b 1
