@echo off
rem ===========================================================================
rem  TurboFetch Download Manager - local Windows build
rem
rem  Produces:
rem    build\dist\TurboFetch.exe               (portable single exe)
rem    build\dist\TurboFetch-Setup-1.0.0.exe   (NSIS installer)
rem
rem  Prerequisites (all standard on a developer machine):
rem    - Python 3.11+          (python.org)
rem    - NSIS 3.x              (nsis.org, "makensis" on PATH)
rem
rem  Usage:  build\build.bat
rem ===========================================================================
setlocal
cd /d "%~dp0.."

set PY=python

echo [1/4] Creating virtual environment...
if not exist .venv (
    %PY% -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat

echo [2/4] Installing dependencies...
python -m pip install --upgrade pip || goto :error
pip install -r requirements.txt || goto :error

echo [3/4] Running test suite...
set QT_QPA_PLATFORM=offscreen
python -m pytest tests -q || goto :error

echo [4/4] Building the executable with PyInstaller...
pyinstaller idm.spec --noconfirm --distpath build\dist --workpath build\pyi_build || goto :error

where makensis >nul 2>nul
if %errorlevel%==0 (
    echo Building NSIS installer...
    makensis build\installer.nsi || goto :error
) else (
    echo WARNING: makensis not found - skipping installer (install NSIS 3.x).
)

echo.
echo ===========================================================================
echo  Build finished:
echo    .\build\dist\TurboFetch.exe
echo    .\build\dist\TurboFetch-Setup-1.0.0.exe   (if NSIS found)
echo ===========================================================================
exit /b 0

:error
echo.
echo BUILD FAILED - see the messages above.
exit /b 1
