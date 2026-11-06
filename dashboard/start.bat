@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PORT=8770"
set "PY="

rem ---- 找一个真能跑的 Python（PATH 里那个可能是商店占位符，要验证一下）----
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
    echo [x] 没找到能用的 Python。
    echo     装一个就行：https://www.python.org/downloads/
    echo     安装时记得勾 "Add python.exe to PATH"。
    echo.
    pause
    exit /b 1
)

echo [*] 使用 Python: %PY%
echo [*] 启动数据服务（这个最小化的黑窗口别关，关了数据就停了）

start "Monitor Server" /min "%PY%" "%~dp0server.py" --port %PORT% --no-open %*
"%PY%" -c "import time;time.sleep(2)" >nul 2>nul

set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"

if exist "%EDGE%" (
    start "" "%EDGE%" --new-window "http://127.0.0.1:%PORT%/" --window-size=1920,1080 --window-position=0,0
    goto :done
)
if exist "%CHROME%" (
    start "" "%CHROME%" --new-window "http://127.0.0.1:%PORT%/" --window-size=1920,1080 --window-position=0,0
    goto :done
)
start "" "http://127.0.0.1:%PORT%/"

:done
echo.
echo   ✓ 窗口已打开：拖到小屏幕 → 按 F 或 F11 全屏 → Esc 退出
echo   ✓ 换尺寸预览：http://127.0.0.1:%PORT%/?w=1024^&h=600
echo   ! 这个 bat 只起画面服务，不含温度服务（CPU 温度会显示 --）。
echo     要完整功能请双击 监控屏.lnk。
echo.
timeout /t 8 >nul
exit /b 0

:trypy
"%~1" -c "import sys" >nul 2>nul
if not errorlevel 1 set "PY=%~1"
exit /b
