@echo off
setlocal

set "PY=%~dp0_python.cmd"
set "ROOT=%~dp0.."

pushd "%ROOT%" >nul
call "%PY%" tests\run_all.py %*
set "RESULT=%ERRORLEVEL%"
popd >nul

if "%RESULT%"=="0" (
  echo.
  echo TRACKER CHECK: PASS
) else (
  echo.
  echo TRACKER CHECK: FAIL
)
exit /b %RESULT%
