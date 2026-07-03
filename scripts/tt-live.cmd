@echo off
setlocal

set "PROXY=C:\Users\yerabhaoui\python-portable\Scripts\ai-token-tracker-proxy.exe"
set "BUDGET=50000"

if not exist "%PROXY%" (
  echo ai-token-tracker-proxy.exe not found at:
  echo %PROXY%
  exit /b 1
)

"%PROXY%" count-prompt --interactive --budget-tokens %BUDGET% %*
