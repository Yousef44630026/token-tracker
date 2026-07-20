@echo off
setlocal EnableExtensions

set "PY=%~dp0_python.cmd"
set "ROOT=%~dp0.."

pushd "%ROOT%" >nul
call "%PY%" scripts\backup_ledger.py %*
set "CODE=%ERRORLEVEL%"
popd >nul
exit /b %CODE%
