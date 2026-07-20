@echo off
setlocal EnableExtensions

set "PY=%~dp0_python.cmd"
set "ROOT=%~dp0.."

if not defined TRACKER_STORE set "TRACKER_STORE=C:\ai-token-tracker-data\collector_events.jsonl"
if not defined TRACKER_LIVE_PORT set "TRACKER_LIVE_PORT=8790"

pushd "%ROOT%" >nul
echo Opening live dashboard at http://127.0.0.1:%TRACKER_LIVE_PORT%
start "" "http://127.0.0.1:%TRACKER_LIVE_PORT%"
call "%PY%" -m tracker.export.live_dashboard --store "%TRACKER_STORE%" --port %TRACKER_LIVE_PORT% %*
set "CODE=%ERRORLEVEL%"
popd >nul
exit /b %CODE%
