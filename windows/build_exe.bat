@echo off
rem Builds dist\Trigno\Trigno.exe: a standalone app that runs on Windows 7 SP1 and later.
rem Needs Python 3.8 (64-bit) so the resulting program still runs on Windows 7; if it is missing,
rem ensure_python38.bat offers to download and install it. The build itself is in build_core.bat.
setlocal
cd /d "%~dp0.."

call windows\ensure_python38.bat || goto :nopython
"%PY38%" -m venv .venv-build || goto :failed
call windows\build_core.bat ".venv-build\Scripts\python.exe" || goto :failed

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
