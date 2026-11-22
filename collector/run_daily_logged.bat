@echo off
REM ============================================================
REM  Wrapper for Windows Task Scheduler.
REM  Runs daily.bat and appends everything to wechat_daily.log.
REM  Keep this file pure ASCII (Windows cmd reads .bat with the
REM  system code page; UTF-8 Chinese would break parsing).
REM ============================================================

chcp 65001 >nul
set "LOG=%~dp0..\wechat_daily.log"
cd /d "%~dp0"

echo. >> "%LOG%"
echo ==== %date% %time% ==== >> "%LOG%"
call daily.bat >> "%LOG%" 2>&1
exit /b %ERRORLEVEL%
