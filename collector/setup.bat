@echo off
REM ============================================================
REM  WeChat Daily Report - step 1 setup launcher (WeChat 4.x)
REM  IMPORTANT: keep this file pure ASCII (see notes below).
REM  All Chinese messages live in wechat4.py.
REM
REM  Run as administrator (right-click > Run as administrator).
REM  WeChat desktop must be running and logged in.
REM
REM  Why pure ASCII: Windows cmd reads .bat with the system code page
REM  (GBK on Chinese Windows). UTF-8 Chinese in a .bat becomes garbage
REM  and breaks command parsing. Keep Chinese in the .py files.
REM ============================================================

chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY=python"
where python >nul 2>nul
if errorlevel 1 set "PY=py -3"

"%PY%" wechat4.py setup %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [FAILED] wechat4.py setup exited with code %RC%. See the messages above.
)
echo.
pause
endlocal & exit /b %RC%
