@echo off
setlocal

set "ROOT=%~dp0.."
set "PY=%~dp0_python.cmd"
set "PYTHONPATH=%ROOT%;%PYTHONPATH%"

pushd "%ROOT%" >nul
call "%PY%" -m tracker.ops.provider_proof %*
set "CODE=%ERRORLEVEL%"
popd >nul
exit /b %CODE%
