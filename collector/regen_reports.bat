@echo off
REM ============================================================
REM  WeChat Daily Report - re-render past reports
REM  Pure ASCII only. Chinese messages live in bridge.py /
REM  summarize_local.py.
REM
REM  regen_reports.bat 2026-11-22 2026-11-23
REM  regen_reports.bat            (no args -> yesterday and the day before)
REM
REM  For each date:
REM    1. pull the raw daily JSON back from Feishu (that is the archive)
REM    2. regenerate the report with the CURRENT format
REM    3. push the new report into the Feishu "report" column
REM
REM  The raw chat records never leave this machine: only the finished
REM  report is pushed.
REM ============================================================

chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY=python"
where python >nul 2>nul
if errorlevel 1 set "PY=py -3"

set "DATES=%*"
if "%DATES%"=="" (
  for /f "usebackq delims=" %%i in (`"%PY%" -c "import datetime as d; t=d.date.today(); print((t-d.timedelta(days=1)).isoformat(), (t-d.timedelta(days=2)).isoformat())"`) do set "DATES=%%i"
)

if "%DATES%"=="" (
  echo [X] Could not work out which dates to use.
  exit /b 1
)

for %%d in (%DATES%) do call :one %%d
goto end

:one
echo.
echo ==== %1 ====
"%PY%" bridge.py pull --date %1
if errorlevel 1 goto failed
"%PY%" summarize_local.py --date %1 --push
if errorlevel 1 goto failed
echo [OK] %1 regenerated and pushed.
goto :eof

:failed
echo.
echo [X] Failed for %1 - send the output above to the developer.
exit /b 1

:end
echo.
echo [OK] All done. Reports are in the "reports" folder.
exit /b 0
