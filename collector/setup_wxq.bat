@echo off
REM ============================================================
REM  WeChat Daily Report - FALLBACK setup launcher (WeChat 4.0 and older)
REM  For WeChat 4.1.10.31+ / 4.2 use setup.bat instead (wxq cannot read those).
REM  IMPORTANT: this file must stay pure ASCII.
REM  Windows cmd reads .bat files with the system code page
REM  (GBK on Chinese Windows). UTF-8 Chinese text in a .bat
REM  turns into garbage and breaks the command parsing.
REM  All Chinese messages live in setup_wxq.py instead.
REM
REM  Run as administrator (right-click > Run as administrator).
REM ============================================================

chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================================
echo  FALLBACK ONLY - this works for WeChat 4.0 and OLDER.
echo  WeChat 4.1.10.31+ / 4.2 CANNOT use wxq.
echo  If you are on 4.1.10.31+ or 4.2, run  setup.bat  instead.
echo ============================================================
echo.

set "PY=python"
where python >nul 2>nul
if errorlevel 1 set "PY=py -3"

"%PY%" setup_wxq.py %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo [FAILED] setup_wxq.py exited with code %RC%. See the messages above.
)
echo.
pause
endlocal & exit /b %RC%
