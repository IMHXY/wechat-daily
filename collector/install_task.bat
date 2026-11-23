@echo off
REM ============================================================
REM  WeChat Daily Report - Windows Task Scheduler installer
REM  Pure ASCII only. Chinese messages live in install_task.py.
REM
REM  install_task.bat            install (default 22:55, silent, no window)
REM  install_task.bat status     show status
REM  install_task.bat uninstall  remove
REM  install_task.bat install --interactive   old mode: shows a window
REM ============================================================

chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY=python"
where python >nul 2>nul
if errorlevel 1 set "PY=py -3"

"%PY%" install_task.py %*
exit /b %ERRORLEVEL%
