@echo off
rem Finds Python 3.8 and sets PY38 to its python.exe. If it is missing, downloads the official
rem Python 3.8.10 (64-bit) installer from python.org, checks its SHA-256 and installs it for the
rem current user only (no admin rights; PATH, the py launcher and .py file associations are left
rem unchanged). Used by build_exe.bat and run_from_source.bat. Exit code 0 = found or installed.
setlocal EnableDelayedExpansion
set "PY38="
set "TARGET=%LOCALAPPDATA%\Programs\Python\Python38"
set "URL=https://www.python.org/ftp/python/3.8.10/python-3.8.10-amd64.exe"
set "SHA256=7628244cb53408b50639d2c1287c659f4e29d3dfdb9084b11aed5870c0c6a48a"
set "INSTALLER=%TEMP%\python-3.8.10-amd64.exe"

call :find
if defined PY38 goto :done

if /i "%PROCESSOR_ARCHITECTURE%"=="x86" if not defined PROCESSOR_ARCHITEW6432 (
    echo This PC runs 32-bit Windows. Trigno needs 64-bit Windows.
    exit /b 1
)

echo.
echo Python 3.8 was not found. This script can download Python 3.8.10 (64-bit, about 28 MB)
echo from python.org and install it for your user account only:
echo     %TARGET%
echo It won't change PATH or your existing Python installations.
echo.
echo Press any key to download and install, or close this window to cancel.
pause >nul

echo Downloading %URL% ...
if exist "%INSTALLER%" del "%INSTALLER%"
rem curl.exe ships with Windows 10 1803 and later. Windows 7 falls back to PowerShell, with TLS 1.2
rem enabled explicitly (python.org requires it).
where curl.exe >nul 2>&1 && curl.exe -fL --retry 2 -o "%INSTALLER%" "%URL%" || del "%INSTALLER%" >nul 2>&1
if not exist "%INSTALLER%" powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "[Net.ServicePointManager]::SecurityProtocol = 3072; (New-Object Net.WebClient).DownloadFile('%URL%', '%INSTALLER%')"
if not exist "%INSTALLER%" goto :download_failed

rem certutil prints the hash on the second line; Windows 7 separates the bytes with spaces.
set "ACTUAL="
for /f "skip=1 delims=" %%h in ('certutil -hashfile "%INSTALLER%" SHA256') do if not defined ACTUAL set "ACTUAL=%%h"
set "ACTUAL=!ACTUAL: =!"
if /i not "!ACTUAL!"=="%SHA256%" (
    echo The downloaded file failed its checksum check, so it was not run.
    del "%INSTALLER%"
    goto :download_failed
)

echo Installing Python 3.8.10. This takes a minute or two...
"%INSTALLER%" /quiet InstallAllUsers=0 "TargetDir=%TARGET%" PrependPath=0 Include_launcher=0 ^
    AssociateFiles=0 Include_test=0 Include_doc=0 Shortcuts=0
set "RESULT=%ERRORLEVEL%"
del "%INSTALLER%" >nul 2>&1
call :find
if defined PY38 (
    echo Python 3.8.10 installed.
    goto :done
)
echo.
echo The Python installer failed (code %RESULT%). On Windows 7, install Service Pack 1 and update
echo KB2533623 from Windows Update, then run this again, or install Python 3.8.10 (64-bit)
echo yourself from %URL%
exit /b 1

:download_failed
echo.
echo Couldn't download Python. On Windows 7 this usually means the system can't use TLS 1.2
echo (it needs .NET Framework 4.5 or later). Download and install Python 3.8.10 (64-bit)
echo yourself, from this PC or by copying the installer from another one:
echo     %URL%
echo Then run this again.
exit /b 1

:find
rem Prefer the py launcher's Python 3.8, then the per-user location this script installs to.
for /f "delims=" %%p in ('py -3.8 -c "import sys; print(sys.executable)" 2^>nul') do set "PY38=%%p"
if not defined PY38 if exist "%TARGET%\python.exe" set "PY38=%TARGET%\python.exe"
exit /b 0

:done
endlocal & set "PY38=%PY38%"
exit /b 0
