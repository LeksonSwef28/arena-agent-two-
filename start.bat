@echo off
REM ============================================================
REM  Arena Unified Bridge — Project-Safe Start (Windows)
REM
REM  Usage:
REM    start.bat "F:\\path\\to\\project" [read|write]
REM
REM  read  = read-only project tools (default)
REM  write = allow fs.create/fs.edit/fs.write inside the workspace
REM
REM  Host shell, installers, network tools, services and desktop/mobile
REM  control stay blocked by ARENA_PROJECT_SAFE regardless of this mode.
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "BRIDGE_DIR=%~dp0"
if "%BRIDGE_DIR:~-1%"=="\" set "BRIDGE_DIR=%BRIDGE_DIR:~0,-1%"
set "PORT=8765"
set "STATE_DIR=!BRIDGE_DIR!\.arena-state"
set "TOKEN_FILE=!STATE_DIR!\token.txt"

if "%~1"=="" (
    echo [ERROR] Explicit project path is required.
    echo Usage: start.bat "F:\path\to\project" [read^|write]
    exit /b 2
)
set "PROJECT_ROOT=%~f1"
if not exist "!PROJECT_ROOT!\." (
    echo [ERROR] Project directory does not exist: !PROJECT_ROOT!
    exit /b 2
)

set "MODE=%~2"
if not defined MODE set "MODE=read"
if /I not "!MODE!"=="read" if /I not "!MODE!"=="write" (
    echo [ERROR] Mode must be read or write.
    exit /b 2
)

set "ARENA_PROJECT_SAFE=1"
set "ARENA_PROJECT_SAFE_WRITES=0"
set "ARENA_PROJECT_SAFE_CODE=0"
if /I "!MODE!"=="write" set "ARENA_PROJECT_SAFE_WRITES=1"
set "ARENA_AGENT_HOME=!STATE_DIR!"

REM --- Find Python without installing anything ---
set "PYTHON="
if exist "!BRIDGE_DIR!\.venv\Scripts\python.exe" set "PYTHON=!BRIDGE_DIR!\.venv\Scripts\python.exe"
if not defined PYTHON (
    for /f "delims=" %%P in ('where python 2^>nul') do if not defined PYTHON set "PYTHON=%%P"
)
if not defined PYTHON (
    echo [ERROR] Python was not found. Project-safe launcher never installs software.
    exit /b 3
)

"!PYTHON!" -c "import aiohttp, psutil, websockets" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Arena runtime dependencies are missing.
    echo Install them manually after review; this launcher will not run pip/winget/choco/scoop.
    exit /b 4
)

if not exist "!STATE_DIR!\." mkdir "!STATE_DIR!" >nul 2>&1
if not exist "!TOKEN_FILE!" (
    "!PYTHON!" -c "import pathlib,secrets; pathlib.Path(r'!TOKEN_FILE!').write_text(secrets.token_urlsafe(32), encoding='utf-8')" >nul 2>&1
)
if not exist "!TOKEN_FILE!" (
    echo [ERROR] Could not create bridge token: !TOKEN_FILE!
    exit /b 5
)

REM --- Never kill an unknown process just because it owns our port ---
set "OCCUPIED_PID="
for /f "tokens=5" %%P in ('netstat -ano 2^>nul ^| findstr ":%PORT% " ^| findstr /I "LISTENING"') do (
    if not defined OCCUPIED_PID set "OCCUPIED_PID=%%P"
)
if defined OCCUPIED_PID (
    echo [STOP] Port %PORT% is already in use by PID !OCCUPIED_PID!.
    echo No process was terminated. Stop/inspect it yourself, then retry.
    exit /b 6
)

echo.
echo ============================================================
echo   Arena Project-Safe Bridge
echo ============================================================
echo   Workspace : !PROJECT_ROOT!
echo   Mode      : !MODE!
echo   Bind      : http://127.0.0.1:%PORT%
echo   Profile   : cautious + project-safe backend gate
echo   Token file: !TOKEN_FILE!
echo.
echo   NOT enabled: owner-shell, auto-install, tunnels, autostart,
echo                arbitrary exec, network/admin/desktop/mobile tools.
echo ============================================================
echo.

"!PYTHON!" -u unified_bridge.py serve --root "!PROJECT_ROOT!" --profile cautious --bind 127.0.0.1 --token-file "!TOKEN_FILE!" --port %PORT%
