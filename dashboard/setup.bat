@echo off
rem ---------------------------------------------------------------------------
rem One-time setup: find a working Python and create the three shortcuts
rem (they have to be generated locally because they point at this machine's
rem  pythonw.exe, so the repo does not ship .lnk files).
rem ---------------------------------------------------------------------------
chcp 65001 >nul
cd /d "%~dp0"

set "PY="
call :trypy python
if not defined PY call :trypy py
if not defined PY call :trypy "C:\Espressif\tools\python\python.exe"
if not defined PY call :trypy "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if not defined PY call :trypy "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not defined PY call :trypy "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PY call :trypy "%ProgramFiles%\Python313\python.exe"
if not defined PY call :trypy "%ProgramFiles%\Python312\python.exe"
if not defined PY call :trypy "%ProgramFiles%\Python311\python.exe"

if not defined PY (
    echo.
    echo [x] No working Python found.
    echo     Install one from https://www.python.org/downloads/
    echo     and tick "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
)

echo [*] Python: %PY%

rem derive pythonw.exe from python.exe
set "PYW=%PY%"
set "PYW=%PYW:python.exe=pythonw.exe%"
if not exist "%PYW%" (
    echo [x] pythonw.exe not found next to %PY%
    pause
    exit /b 1
)

echo [*] Creating shortcuts with %PYW%
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0make_shortcuts.ps1" -Pythonw "%PYW%"
if errorlevel 1 (
    echo [x] Shortcut creation failed.
    pause
    exit /b 1
)

echo.
echo [ok] Done. Now double-click the shortcut to start the dashboard.
echo      First run will ask for UAC once (LibreHardwareMonitor needs admin).
echo.
pause
exit /b 0

:trypy
"%~1" -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY=%~1"
exit /b
