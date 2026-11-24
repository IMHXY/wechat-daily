@echo off
REM ============================================================
REM  WeChat Daily Report - scope (whitelist) helper
REM  Pure ASCII only. Chinese messages live in wechat4.py.
REM
REM  set_scope.bat           open the whitelist file in Notepad,
REM                          then apply it to config.env
REM  set_scope.bat show      show current scope settings
REM  set_scope.bat clear     clear the whitelist
REM  set_scope.bat preview   preview which chats enter the report
REM
REM  Put one group name per line in "include.txt" (UTF-8).
REM  Lines starting with # are comments. Blank lines are ignored.
REM ============================================================

chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY=python"
where python >nul 2>nul
if errorlevel 1 set "PY=py -3"

set "FILE=%~dp0include.txt"
if not exist "%FILE%" (
  if exist "%~dp0include.example.txt" (
    copy /y "%~dp0include.example.txt" "%FILE%" >nul
  ) else (
    echo. > "%FILE%"
  )
  echo [i] created "%FILE%"
  echo.
)

if /i "%~1"=="show" goto show
if /i "%~1"=="clear" goto clear
if /i "%~1"=="preview" goto preview

REM default: edit the file first, then apply
echo [i] Editing "%FILE%" - one group name per line, save and close.
notepad "%FILE%"
echo.

:apply
"%PY%" wechat4.py scope --include-file "%FILE%"
if errorlevel 1 goto failed
echo.
echo [OK] Whitelist applied. Use "set_scope.bat show" to check.
goto end

:show
"%PY%" wechat4.py scope
goto end

:clear
"%PY%" wechat4.py scope --clear-include
goto end

:preview
"%PY%" wechat4.py scope --preview
goto end

:failed
echo.
echo [X] Failed. Send the output above to the developer.
exit /b 1

:end
exit /b %ERRORLEVEL%
