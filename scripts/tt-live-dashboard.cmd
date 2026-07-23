@echo off
setlocal EnableExtensions

set "PY=%~dp0_python.cmd"
set "ROOT=%~dp0.."
set "PYTHONPATH=%ROOT%;%PYTHONPATH%"

if not defined TRACKER_STORE set "TRACKER_STORE=C:\ai-token-tracker-data\collector_events.jsonl"
if not defined TRACKER_LIVE_PORT set "TRACKER_LIVE_PORT=8790"
if not defined TRACKER_LIVE_OPEN set "TRACKER_LIVE_OPEN=true"

pushd "%ROOT%" >nul
echo Starting live dashboard at http://127.0.0.1:%TRACKER_LIVE_PORT%
if /I "%TRACKER_LIVE_OPEN%"=="false" goto runserver
start "" /b powershell.exe -NoProfile -WindowStyle Hidden -Command ^
  "$url='http://127.0.0.1:%TRACKER_LIVE_PORT%'; for($i=0;$i -lt 40;$i++){try{Invoke-WebRequest -UseBasicParsing $url -TimeoutSec 2 ^| Out-Null; Start-Process $url; exit 0}catch{Start-Sleep -Milliseconds 250}}; exit 1"
:runserver
call "%PY%" "%~dp0run_live_dashboard.py" --store "%TRACKER_STORE%" --port %TRACKER_LIVE_PORT% %*
set "CODE=%ERRORLEVEL%"
popd >nul
exit /b %CODE%
