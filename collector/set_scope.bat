@echo off
REM ============================================================
REM  WeChat Daily Report - scope (whitelist) helper
REM  Pure ASCII only. Chinese messages live in wechat4.py.
REM
REM  set_scope.bat                open the whitelist file in Notepad,
REM                               then apply it to config.env
REM  set_scope.bat show           show current scope settings
REM  set_scope.bat clear          clear the whitelist
REM  set_scope.bat preview        preview which chats enter the report
REM  set_scope.bat window 10      context window: keep 10 messages
REM                               before and after each of my messages
REM                               (0 = only my own, -1 = keep everything)
REM  set_scope.bat max 20         whitelist group I did not speak in:
REM                               keep at most the latest 20 messages
REM                               (0 = keep none of them)
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
if /i "%~1"=="window" goto window
if /i "%~1"=="max" goto max

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

:window
if "%~2"=="" goto neednum
echo [i] Setting context window to %~2 ...
"%PY%" wechat4.py scope --window %~2
goto end

:max
if "%~2"=="" goto neednum
echo [i] Setting whitelist limit to %~2 ...
"%PY%" wechat4.py scope --include-max %~2
goto end

:neednum
echo.
echo [X] Please give a number.
echo     Examples:
echo       set_scope.bat window 10
echo       set_scope.bat window 0
echo       set_scope.bat max 20
exit /b 1

:failed
echo.
echo [X] Failed. Send the output above to the developer.
exit /b 1

:end
exit /b %ERRORLEVEL%
