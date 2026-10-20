@echo off
rem Start Sedjem from source on Windows (settings: app\config.toml).
rem   app.cmd              desktop window
rem   app.cmd --browser    in a browser window instead
rem   app.cmd --server     only the server, for any browser at http://127.0.0.1:8765
rem Setup once (Python 3.12):  py -3.12 -m venv .venv
rem                            .venv\Scripts\pip install -r requirements.txt -r requirements-desktop.txt
rem It starts without a console; to see why it doesn't open:  .venv\Scripts\python -m app.desktop
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Sedjem isn't set up yet. Install Python 3.12, then run these once in this folder:
  echo   py -3.12 -m venv .venv
  echo   .venv\Scripts\pip install -r requirements.txt -r requirements-desktop.txt
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m app.desktop %*
