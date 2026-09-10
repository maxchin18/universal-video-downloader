@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call run.bat
".venv\Scripts\python.exe" -m pip install --quiet pyinstaller
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed ^
  --name "VideoDownloader" ^
  --icon "assets\app.ico" ^
  --add-data "ui;ui" ^
  --collect-all yt_dlp ^
  --collect-all yt_dlp_ejs ^
  --collect-all imageio_ffmpeg ^
  app.py
echo.
echo Done. The portable app is in dist\VideoDownloader\VideoDownloader.exe
pause
