@echo off
setlocal
for %%I in ("%~dp0..") do set "REPO_ROOT=%%~fI"
if defined TRACKER_PYTHON (
  set "PYTHON_EXE=%TRACKER_PYTHON%"
) else (
  set "PYTHON_EXE=python"
)
pushd "%REPO_ROOT%" >nul
"%PYTHON_EXE%" -m tracker.ops.bedrock_stream_smoke %*
set "EXIT_CODE=%ERRORLEVEL%"
popd >nul
exit /b %EXIT_CODE%
