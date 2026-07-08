@echo off
setlocal

set "PY=C:\Users\yerabhaoui\python-portable\python.exe"
if not exist "%PY%" set "PY=python"

set "ROOT=%~dp0.."
pushd "%ROOT%" >nul

"%PY%" -m tracker.ops.doctor %*
set "CODE=%ERRORLEVEL%"

popd >nul
exit /b %CODE%
