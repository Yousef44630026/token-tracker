@echo off
setlocal

set "PROXY=C:\Users\yerabhaoui\python-portable\Scripts\ai-token-tracker-proxy.exe"
set "STORE=%~1"

if "%STORE%"=="" set "STORE=codex_live.jsonl"

if not exist "%PROXY%" (
  echo ai-token-tracker-proxy.exe not found at:
  echo %PROXY%
  exit /b 1
)

"%PROXY%" report --store "%STORE%"
