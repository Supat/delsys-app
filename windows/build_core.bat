@echo off
rem Shared by build_exe.bat and the GitHub Actions workflow. Usage: build_core.bat <python.exe>
rem Installs the Windows 7 stack and PyInstaller into that Python, builds dist\Trigno, checks that
rem numpy's OpenBLAS DLL sits next to Trigno.exe, and runs the exe once in self-test mode.
setlocal
set "PY=%~1"
if "%PY%"=="" set "PY=python"
cd /d "%~dp0.."
if not exist build mkdir build

"%PY%" -m pip install --upgrade "pip<24.1"
"%PY%" -m pip install -r windows\requirements-win7.txt "pyinstaller==5.13.2" "pyinstaller-hooks-contrib==2023.10" || exit /b 1
"%PY%" -m pip install --no-deps . || exit /b 1

rem numpy's C extension needs the OpenBLAS DLL from site-packages\numpy\.libs. PyInstaller's numpy
rem hook normally copies it next to Trigno.exe, but builds have shipped without it (the app then dies
rem with "DLL load failed while importing _multiarray_umath"), so check for it and pass it explicitly.
"%PY%" -c "import numpy" || (
    echo.
    echo numpy does not work in the build environment, so the built app would not work either.
    echo Antivirus software may have removed numpy's OpenBLAS DLL from site-packages\numpy\.libs.
    echo Check its quarantine or exclude this folder, delete .venv-build, then run this again.
    exit /b 1
)
"%PY%" -c "import glob, os, sys, numpy; d = glob.glob(os.path.join(os.path.dirname(numpy.__file__), '.libs', '*openblas*.dll')); print(d[0] if d else ''); sys.exit(0 if d else 1)" > build\openblas.txt || (
    echo.
    echo numpy's OpenBLAS DLL ^(site-packages\numpy\.libs\libopenblas*.dll^) is missing from the
    echo build environment. Delete .venv-build and run this again; if it is still missing, check
    echo whether antivirus software removes it.
    exit /b 1
)
set /p OPENBLAS=<build\openblas.txt
echo Bundling %OPENBLAS%

"%PY%" -m PyInstaller --noconfirm --clean --windowed ^
    --name Trigno ^
    --paths . ^
    --icon trigno\gui\trigno.ico ^
    --add-data "trigno\gui\trigno.ico;assets" ^
    --add-binary "%OPENBLAS%;." ^
    --collect-all pylsl ^
    --distpath dist --workpath build ^
    windows\trigno_gui.py || exit /b 1

if not exist "dist\Trigno\libopenblas*.dll" (
    echo The build is missing numpy's OpenBLAS DLL next to Trigno.exe.
    exit /b 1
)
echo Running the self-test...
start "" /wait "dist\Trigno\Trigno.exe" --selftest "build\selftest.txt"
if errorlevel 1 (
    echo The self-test failed:
    type "build\selftest.txt"
    exit /b 1
)
echo Self-test: ok
exit /b 0
