@echo off
setlocal EnableExtensions

set "PY=%~dp0_python.cmd"
set "ROOT=%~dp0.."

if not defined TRACKER_COLLECTOR_URL set "TRACKER_COLLECTOR_URL=http://127.0.0.1:8787"

pushd "%ROOT%" >nul
call "%PY%" scripts\import_claude_to_collector.py --collector "%TRACKER_COLLECTOR_URL%"
set "CODE=%ERRORLEVEL%"
popd >nul
exit /b %CODE%
