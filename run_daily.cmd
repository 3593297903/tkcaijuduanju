@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0runtime.local.cmd" call "%~dp0runtime.local.cmd"
if not defined TKMONITOR_PYTHON set "TKMONITOR_PYTHON=python"
if not defined TKMONITOR_CONFIG set "TKMONITOR_CONFIG=%~dp0config.local.json"
"%TKMONITOR_PYTHON%" -X utf8 -m tkmonitor --config "%TKMONITOR_CONFIG%" collect --report --metric frontend %*
set "TKMONITOR_EXIT=%ERRORLEVEL%"
exit /b %TKMONITOR_EXIT%
