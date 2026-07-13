@echo off
setlocal

set "PY=%~dp0_python.cmd"

set "ROOT=%~dp0.."
pushd "%ROOT%" >nul

"%PY%" -m tracker.ops.doctor %*
set "CODE=%ERRORLEVEL%"

popd >nul
exit /b %CODE%
