@echo off
setlocal EnableExtensions

set "PY=%~dp0_python.cmd"
set "ROOT=%~dp0.."

if not defined TRACKER_STORE set "TRACKER_STORE=C:\ai-token-tracker-data\collector_events.jsonl"
if not defined TRACKER_HOST set "TRACKER_HOST=127.0.0.1"
if not defined TRACKER_PORT set "TRACKER_PORT=8787"
if not defined TRACKER_DURABLE set "TRACKER_DURABLE=true"
if not defined TRACKER_RESTART_DELAY_SECONDS set "TRACKER_RESTART_DELAY_SECONDS=10"
set "PYTHONUNBUFFERED=1"

pushd "%ROOT%" >nul

:supervise
call "%PY%" -m api.main
set "CODE=%ERRORLEVEL%"
echo [%DATE% %TIME%] collector exited with code %CODE%; restarting in %TRACKER_RESTART_DELAY_SECONDS%s 1>&2
timeout /t %TRACKER_RESTART_DELAY_SECONDS% /nobreak >nul
goto supervise
