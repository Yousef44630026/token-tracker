@echo off
setlocal EnableExtensions

set "PY=%~dp0_python.cmd"
set "ROOT=%~dp0.."

if not defined TRACKER_STORE set "TRACKER_STORE=C:\ai-token-tracker-data\collector_events.jsonl"
if not defined TRACKER_HOST set "TRACKER_HOST=127.0.0.1"
if not defined TRACKER_PORT set "TRACKER_PORT=8787"
if not defined TRACKER_DURABLE set "TRACKER_DURABLE=true"
set "PYTHONUNBUFFERED=1"

pushd "%ROOT%" >nul
call "%PY%" -m api.main
set "CODE=%ERRORLEVEL%"
popd >nul

exit /b %CODE%
