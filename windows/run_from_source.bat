@echo off
rem Runs the Trigno app from source with Python 3.8 (the newest Python for Windows 7). If Python 3.8
rem is missing, ensure_python38.bat offers to download and install it.
rem First run creates a virtual environment next to this repo; later runs just start the app.
setlocal
cd /d "%~dp0.."

if exist ".venv-win\Scripts\python.exe" goto :run
echo Creating Python 3.8 environment...
call windows\ensure_python38.bat || goto :nopython
"%PY38%" -m venv .venv-win || goto :failed
".venv-win\Scripts\python.exe" -m pip install --upgrade "pip<24.1"
".venv-win\Scripts\python.exe" -m pip install -r windows\requirements-win7.txt || goto :failed
".venv-win\Scripts\python.exe" -m pip install --no-deps -e . || goto :failed

:run
start "" ".venv-win\Scripts\pythonw.exe" -m trigno.gui %*
exit /b 0

:nopython
pause
exit /b 1

:failed
echo.
echo Installing dependencies failed. See the messages above.
pause
exit /b 1
