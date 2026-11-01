@echo off
rem Runs every test suite: Python (pytest) and frontend (typecheck, vitest, production build).
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" ( echo Run start_fieldledger.bat once first to create .venv. & exit /b 1 )
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -e ".[web-dev]"
".venv\Scripts\python.exe" -m pytest -q -rs
if errorlevel 1 exit /b 1
pushd frontend
call npm ci
call npm run typecheck
if errorlevel 1 ( popd & exit /b 1 )
call npm test
if errorlevel 1 ( popd & exit /b 1 )
call npm run build
popd
