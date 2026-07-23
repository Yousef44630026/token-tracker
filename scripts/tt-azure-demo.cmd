@echo off
setlocal EnableExtensions

set "SMOKE=%~dp0tt-azure-smoke.cmd"
if not defined TRACKER_COLLECTOR_URL set "TRACKER_COLLECTOR_URL=http://127.0.0.1:8787"

echo Azure Foundry end-to-end token demo
if defined TRACKER_AZURE_DEMO_SURFACE (
    echo surface: %TRACKER_AZURE_DEMO_SURFACE%
) else (
    echo surfaces: all available
)
echo collector: %TRACKER_COLLECTOR_URL%

if defined TRACKER_AZURE_DEMO_SURFACE (
    call "%SMOKE%" --suite demo --surface "%TRACKER_AZURE_DEMO_SURFACE%" --require-live --collector-url "%TRACKER_COLLECTOR_URL%" %*
) else (
    call "%SMOKE%" --suite demo --require-live --collector-url "%TRACKER_COLLECTOR_URL%" %*
)
set "CODE=%ERRORLEVEL%"

if "%CODE%"=="0" (
    echo Live dashboard: http://127.0.0.1:8790
) else (
    echo One or more scenario checks failed. Review the summary above.
    echo Successfully billed calls may still have a verified collector trace and remain counted.
)

exit /b %CODE%
