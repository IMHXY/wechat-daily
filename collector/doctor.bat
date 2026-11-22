@echo off
REM ============================================================
REM  WeChat Daily Report - doctor launcher
REM  IMPORTANT: keep this file pure ASCII (see setup_wxq.bat).
REM  All Chinese messages live in doctor.py.
REM ============================================================

chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY=python"
where python >nul 2>nul
if errorlevel 1 set "PY=py -3"

"%PY%" doctor.py %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [FAILED] doctor.py exited with code %RC%.
)
echo.
pause
endlocal & exit /b %RC%
