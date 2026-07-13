@echo off
setlocal

set "PY=%~dp0_python.cmd"
set "ROOT=%~dp0.."
set "STORE=%~1"

if "%STORE%"=="" set "STORE=codex_live.jsonl"

pushd "%ROOT%" >nul
"%PY%" -m tracker.proxy.cli report --store "%STORE%"
set "CODE=%ERRORLEVEL%"

popd >nul
exit /b %CODE%
