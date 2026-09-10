@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [setup] Creating virtual environment ...
  py -3 -m venv .venv 2>nul || python -m venv .venv || goto :nopython
)

if not exist ".venv\.deps_ok" (
  echo [setup] Installing packages, this takes a minute on first run ...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :pipfail
  echo ok> ".venv\.deps_ok"
)

start "" ".venv\Scripts\pythonw.exe" app.py
exit /b 0

:nopython
echo.
echo Python 3 was not found. Install it from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" during setup, then run this file again.
pause
exit /b 1

:pipfail
echo.
echo Package installation failed. Check your network connection and run again.
pause
exit /b 1
