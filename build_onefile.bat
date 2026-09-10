@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call run.bat
".venv\Scripts\python.exe" -m pip install --quiet pyinstaller pillow
if not exist "assets\app.ico" ".venv\Scripts\python.exe" assets\make_icon.py
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "VideoDownloader-portable" ^
  --icon "assets\app.ico" ^
  --add-data "ui;ui" ^
  --collect-all yt_dlp ^
  --collect-all yt_dlp_ejs ^
  --collect-all imageio_ffmpeg ^
  app.py
echo.
echo Done. Single-file app: dist\VideoDownloader-portable.exe
pause
