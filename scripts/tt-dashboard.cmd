@echo off
setlocal EnableExtensions

set "PY=%~dp0_python.cmd"
set "ROOT=%~dp0.."

pushd "%ROOT%" >nul
call "%PY%" -m tracker.reporting.excel_dashboard %*
set "CODE=%ERRORLEVEL%"
popd >nul

exit /b %CODE%
