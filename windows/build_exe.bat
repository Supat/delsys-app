@echo off
rem Builds dist\Trigno\Trigno.exe: a standalone app that runs on Windows 7 SP1 and later.
rem Needs Python 3.8 (64-bit) so the resulting program still runs on Windows 7; if it is missing,
rem ensure_python38.bat offers to download and install it.
setlocal
cd /d "%~dp0.."

call windows\ensure_python38.bat || goto :nopython
"%PY38%" -m venv .venv-build || goto :failed
".venv-build\Scripts\python.exe" -m pip install --upgrade "pip<24.1"
".venv-build\Scripts\python.exe" -m pip install -r windows\requirements-win7.txt "pyinstaller==5.13.2" "pyinstaller-hooks-contrib==2023.10" || goto :failed
".venv-build\Scripts\python.exe" -m pip install --no-deps . || goto :failed

".venv-build\Scripts\pyinstaller.exe" --noconfirm --clean --windowed ^
    --name Trigno ^
    --paths . ^
    --icon trigno\gui\trigno.ico ^
    --add-data "trigno\gui\trigno.ico;assets" ^
    --collect-all pylsl ^
    --distpath dist --workpath build ^
    windows\trigno_gui.py || goto :failed

echo.
echo Built dist\Trigno\Trigno.exe
echo Copy the whole dist\Trigno folder to the measurement PC and run Trigno.exe.
pause
exit /b 0

:nopython
pause
exit /b 1

:failed
echo Build failed. See the messages above.
pause
exit /b 1
