@echo off
setlocal

set "PY=%~dp0_python.cmd"
set "ROOT=%~dp0.."
set "BUDGET=50000"

pushd "%ROOT%" >nul
"%PY%" -m tracker.proxy.cli count-prompt --interactive --budget-tokens %BUDGET% %*
set "CODE=%ERRORLEVEL%"

popd >nul
exit /b %CODE%
