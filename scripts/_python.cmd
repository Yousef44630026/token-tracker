@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem Resolve a supported Python without relying on a developer-specific absolute path.
rem Resolution order: explicit override, active/project virtualenv, conventional portable
rem install, Windows launcher, then PATH. The called interpreter owns the exit code.
set "ROOT=%~dp0.."

if defined AI_TOKEN_TRACKER_PYTHON (
  if not exist "%AI_TOKEN_TRACKER_PYTHON%" (
    echo AI_TOKEN_TRACKER_PYTHON does not exist: "%AI_TOKEN_TRACKER_PYTHON%" 1>&2
    exit /b 9009
  )
  "%AI_TOKEN_TRACKER_PYTHON%" %*
  exit /b !ERRORLEVEL!
)

if defined VIRTUAL_ENV if exist "%VIRTUAL_ENV%\Scripts\python.exe" (
  "%VIRTUAL_ENV%\Scripts\python.exe" %*
  exit /b !ERRORLEVEL!
)

if exist "%ROOT%\.venv\Scripts\python.exe" (
  "%ROOT%\.venv\Scripts\python.exe" %*
  exit /b !ERRORLEVEL!
)

if exist "%USERPROFILE%\python-portable\python.exe" (
  "%USERPROFILE%\python-portable\python.exe" %*
  exit /b !ERRORLEVEL!
)

where py >nul 2>nul
if not errorlevel 1 (
  py -3 %*
  exit /b !ERRORLEVEL!
)

where python >nul 2>nul
if not errorlevel 1 (
  python %*
  exit /b %ERRORLEVEL%
)

echo Python 3.11+ was not found. Create .venv or set AI_TOKEN_TRACKER_PYTHON. 1>&2
exit /b 9009
