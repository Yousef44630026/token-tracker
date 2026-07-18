@echo off
setlocal
call "%~dp0_python.cmd" -m tracker.ops.retention %*
exit /b %ERRORLEVEL%
