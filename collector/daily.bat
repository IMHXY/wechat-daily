@echo off
REM ============================================================
REM  WeChat Daily Report - daily runner launcher
REM  IMPORTANT: keep this file pure ASCII (see setup_wxq.bat).
REM  All Chinese messages live in daily.py.
REM
REM  Put this in Windows Task Scheduler, trigger 22:55 daily.
REM ============================================================

chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY=python"
where python >nul 2>nul
if errorlevel 1 set "PY=py -3"

"%PY%" daily.py %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [FAILED] daily.py exited with code %RC%. See the messages above.
)
endlocal & exit /b %RC%
