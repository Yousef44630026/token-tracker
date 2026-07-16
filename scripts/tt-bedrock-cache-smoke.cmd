@echo off
setlocal
call "%~dp0_python.cmd" -m tracker.ops.bedrock_cache_smoke %*
exit /b %ERRORLEVEL%
